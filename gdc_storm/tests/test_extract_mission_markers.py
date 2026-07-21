import os
import shutil
import tempfile
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from gdc_storm.arma3map import (
    get_arma3map_config,
    resolve_marker_color_css,
    with_local_map_assets,
)
from gdc_storm.models import MapName, Mission
from gdc_storm.pbo_extract import load_markers_from_mission, markers_abs_path, save_markers_to_storage
from gdc_storm.views import (
    create_mission_from_pbo,
    mission_pbo_candidate_names,
    update_mission_from_pbo,
)


SQM_WITH_MARKER = (
    b'version=54;\n'
    b'class Entities {\n'
    b'  class Item0 {\n'
    b'    name="HC_Slot";\n'
    b'    isPlayable=1;\n'
    b'    type="HeadlessClient_F";\n'
    b'  };\n'
    b'  class Item1 {\n'
    b'    dataType="Marker";\n'
    b'    position[]={1000,0,2000};\n'
    b'    name="checkpoint_east";\n'
    b'    text="Checkpoint EST";\n'
    b'    markerType="hd_flag";\n'
    b'    colorName="ColorRed";\n'
    b'  };\n'
    b'};\n'
)


def _make_mission_maker(user):
    group, _ = Group.objects.get_or_create(name='Mission Maker')
    user.groups.add(group)
    return user


class Arma3MapConfigTest(TestCase):
    def test_known_map(self):
        cfg = with_local_map_assets(get_arma3map_config('altis'))
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg['map_key'], 'altis')
        self.assertIn('maps/altis.js', cfg['map_js_url'])
        self.assertIn('/static/gdc_storm/markers', cfg['markers_icon_base'])
        self.assertIn('leaflet.js', cfg['leaflet_js_url'])

    def test_resolve_marker_icon_filename(self):
        from gdc_storm.arma3map import is_zone_marker_type, resolve_marker_icon_filename
        self.assertEqual(resolve_marker_icon_filename('hd_flag'), 'hd_flag.png')
        self.assertEqual(resolve_marker_icon_filename('hd_flag_noShadow'), 'hd_flag.png')
        self.assertEqual(resolve_marker_icon_filename('b_inf'), 'b_inf.png')
        self.assertEqual(resolve_marker_icon_filename('o_inf'), 'o_inf.png')
        self.assertEqual(resolve_marker_icon_filename('loc_Transmitter'), 'loc_Transmitter.png')
        self.assertEqual(resolve_marker_icon_filename('rectangle'), 'hd_dot.png')
        self.assertTrue(is_zone_marker_type('RECTANGLE'))
        self.assertTrue(is_zone_marker_type('ellipse'))

    def test_resolve_marker_color_css(self):
        self.assertEqual(resolve_marker_color_css('ColorRed'), '#FF0000')
        self.assertEqual(resolve_marker_color_css('colorred'), '#FF0000')
        self.assertEqual(resolve_marker_color_css('Red'), '#FF0000')
        self.assertEqual(resolve_marker_color_css('#aabbcc'), '#aabbcc')
        self.assertEqual(resolve_marker_color_css('Default'), '#000000')
        self.assertIsNone(resolve_marker_color_css(''))
        self.assertIsNone(resolve_marker_color_css('UnknownTint'))

    def test_unknown_map(self):
        self.assertIsNone(get_arma3map_config('unknown_map_xyz'))

    def test_case_insensitive(self):
        cfg = get_arma3map_config('Altis')
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg['map_key'], 'altis')

    def test_cup_chernarus_alias(self):
        cfg = get_arma3map_config('cup_chernarus_a3')
        self.assertIsNotNone(cfg)
        # Cle native Arma3Map (pas un alias vers chernarus vanilla)
        self.assertEqual(cfg['map_key'], 'cup_chernarus_a3')
        self.assertIn('maps/cup_chernarus_a3.js', cfg['map_js_url'])

    def test_fixed_aliases(self):
        self.assertEqual(get_arma3map_config('isladual3')['map_key'], 'isladuala3')
        self.assertEqual(get_arma3map_config('lingor')['map_key'], 'lingor3')
        self.assertEqual(get_arma3map_config('wl_roche')['map_key'], 'wl_rosche')
        self.assertEqual(get_arma3map_config('brf_sumava')['map_key'], 'brf_sumava')
        self.assertEqual(get_arma3map_config('swu_public_rhode_map')['map_key'], 'swu_public_rhode_map')

    def test_enoch_alias(self):
        cfg = get_arma3map_config('enoch')
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg['map_key'], 'enoch')
        self.assertEqual(get_arma3map_config('livonia')['map_key'], 'enoch')


class MarkerUploadIntegrationTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = _make_mission_maker(User.objects.create_user(username='maker', password='pass'))
        MapName.objects.create(code_name='altis', display_name='Altis')
        self.media_dir = tempfile.mkdtemp()
        self.pbo_dir = tempfile.mkdtemp()
        self.storage_override = override_settings(
            MEDIA_ROOT=self.media_dir,
            MISSIONS_MARKERS_STORAGE_PATH='missions/markers',
            MISSIONS_PBO_STORAGE_PATH=self.pbo_dir,
        )
        self.storage_override.enable()
        default_storage.location = self.media_dir
        self.temp_pbo = os.path.join(tempfile.gettempdir(), 'marker_test.pbo')
        with open(self.temp_pbo, 'wb') as f:
            f.write(b'fake pbo content')

    def tearDown(self):
        self.storage_override.disable()
        if os.path.exists(self.temp_pbo):
            os.remove(self.temp_pbo)
        shutil.rmtree(self.media_dir, ignore_errors=True)
        shutil.rmtree(self.pbo_dir, ignore_errors=True)

    def _mock_pbo_entry(self, data):
        return type('PboEntry', (), {'data': data})()

    @patch('gdc_storm.views.save_pbo_to_storage')
    @patch('gdc_storm.views.extract_briefing_from_pbo', return_value=([], []))
    @patch('gdc_storm.views.extract_mission_data_from_pbo')
    @patch('gdc_storm.views.PBOFile.read_file')
    def test_create_mission_writes_markers_file(
        self, mock_read, mock_extract, _mock_briefing, _mock_save
    ):
        pbo = {'mission.sqm': self._mock_pbo_entry(SQM_WITH_MARKER)}
        mock_read.return_value = pbo
        mock_extract.return_value = (
            {
                'author': 'A',
                'onLoadMission': 'B',
                'overviewText': 'C',
                'loadScreen': None,
                'minPlayers': '5',
            },
            [],
        )
        request = self.factory.post('/')
        request.user = self.user
        mission, _msg = create_mission_from_pbo(
            request,
            self.temp_pbo,
            'CPC-CO[20]-MarkerTest-V1.altis.pbo',
            'CPC-CO[20]-MarkerTest',
            'CO',
            20,
            '1',
            'altis',
            strict=False,
        )
        self.assertIsNotNone(mission)
        mission.refresh_from_db()
        self.assertTrue(mission.markers_file.endswith('.json'))
        self.assertTrue(default_storage.exists(mission.markers_file))
        markers = load_markers_from_mission(mission)
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]['name'], 'checkpoint_east')
        raw = Mission.objects.values('markers_file').get(pk=mission.pk)
        self.assertEqual(list(raw.keys()), ['markers_file'])

    @patch('gdc_storm.views.save_pbo_to_storage')
    @patch('gdc_storm.views.extract_briefing_from_pbo', return_value=([], []))
    @patch('gdc_storm.views.extract_mission_data_from_pbo')
    @patch('gdc_storm.views.PBOFile.read_file')
    def test_update_mission_replaces_markers_file(
        self, mock_read, mock_extract, _mock_briefing, _mock_save
    ):
        mission = Mission.objects.create(
            name='CPC-CO[20]-MarkerUpd',
            user=self.user,
            authors='A',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
            markers_file='missions/markers/old.json',
        )
        os.makedirs(os.path.join(self.media_dir, 'missions', 'markers'), exist_ok=True)
        with default_storage.open('missions/markers/old.json', 'wb') as f:
            f.write(b'[]')

        pbo = {'mission.sqm': self._mock_pbo_entry(SQM_WITH_MARKER)}
        mock_read.return_value = pbo
        mock_extract.return_value = (
            {'author': 'A', 'onLoadMission': 'B', 'overviewText': 'C', 'loadScreen': None, 'minPlayers': None},
            [],
        )
        request = self.factory.post('/')
        request.user = self.user
        update_mission_from_pbo(
            request, mission, self.temp_pbo,
            'CPC-CO[20]-MarkerUpd-V2.altis.pbo',
            'CO', 20, '2', 'altis', strict=False,
        )
        mission.refresh_from_db()
        self.assertNotEqual(mission.markers_file, 'missions/markers/old.json')
        self.assertFalse(default_storage.exists('missions/markers/old.json'))
        self.assertTrue(default_storage.exists(mission.markers_file))

    @patch('gdc_storm.views.resolve_pbo_on_disk')
    def test_delete_mission_removes_markers_file(self, mock_resolve):
        client = Client()
        client.force_login(self.user)
        mission = Mission.objects.create(
            name='CPC-CO[20]-MarkerDel',
            user=self.user,
            authors='A',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
        )
        path = save_markers_to_storage(
            [{'name': 'm', 'text': '', 'x': 1.0, 'z': 2.0, 'type': '', 'color': ''}],
            mission_id=mission.id,
        )
        mission.markers_file = path
        mission.save(update_fields=['markers_file'])
        mock_resolve.return_value = None
        client.post(reverse('delete_mission', args=[mission.id]))
        self.assertFalse(default_storage.exists(path))
        self.assertFalse(Mission.objects.filter(pk=mission.id).exists())


class ExtractMissionMarkersCommandTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u', password='pass')
        MapName.objects.create(code_name='altis', display_name='Altis')
        self.media_dir = tempfile.mkdtemp()
        self.pbo_dir = tempfile.mkdtemp()
        self.override = override_settings(
            MEDIA_ROOT=self.media_dir,
            MISSIONS_MARKERS_STORAGE_PATH='missions/markers',
            MISSIONS_PBO_STORAGE_PATH=self.pbo_dir,
        )
        self.override.enable()
        default_storage.location = self.media_dir
        self.mission = Mission.objects.create(
            name='CPC-CO[20]-CmdMarker',
            user=self.user,
            authors='A',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
        )

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media_dir, ignore_errors=True)
        shutil.rmtree(self.pbo_dir, ignore_errors=True)

    def _write_pbo(self):
        pbo_name = mission_pbo_candidate_names(self.mission)[0]
        path = os.path.join(self.pbo_dir, pbo_name)
        with open(path, 'wb') as f:
            f.write(b'placeholder')
        return path

    @patch('yapbol.PBOFile.read_file')
    def test_command_dry_run(self, mock_read):
        mock_read.return_value = {'mission.sqm': type('E', (), {'data': SQM_WITH_MARKER})()}
        self._write_pbo()
        call_command('extract_mission_markers', '--dry-run')
        self.mission.refresh_from_db()
        self.assertEqual(self.mission.markers_file, '')

    @patch('yapbol.PBOFile.read_file')
    def test_command_apply_writes_markers(self, mock_read):
        mock_read.return_value = {'mission.sqm': type('E', (), {'data': SQM_WITH_MARKER})()}
        self._write_pbo()
        call_command('extract_mission_markers')
        self.mission.refresh_from_db()
        self.assertTrue(self.mission.markers_file)
        self.assertEqual(len(load_markers_from_mission(self.mission)), 1)

    def test_command_skip_missing_pbo(self):
        out = StringIO()
        call_command('extract_mission_markers', stdout=out)
        self.assertIn('PBO absent', out.getvalue())

    @patch('yapbol.PBOFile.read_file')
    def test_command_skip_binarized(self, mock_read):
        mock_read.return_value = {'mission.sqm': type('E', (), {'data': b'notversion'})()}
        self._write_pbo()
        out = StringIO()
        call_command('extract_mission_markers', stdout=out)
        self.mission.refresh_from_db()
        self.assertEqual(self.mission.markers_file, '')
        self.assertIn('binaris', out.getvalue())

    @patch('yapbol.PBOFile.read_file')
    def test_command_skip_when_already_extracted_unless_force(self, mock_read):
        self.mission.markers_file = 'missions/markers/existing.json'
        self.mission.save(update_fields=['markers_file'])
        self._write_pbo()
        call_command('extract_mission_markers')
        mock_read.assert_not_called()

    @patch('yapbol.PBOFile.read_file')
    def test_command_repair_missing_same_path(self, mock_read):
        mock_read.return_value = {'mission.sqm': type('E', (), {'data': SQM_WITH_MARKER})()}
        self._write_pbo()
        rel_path = f'missions/markers/{self.mission.id}.json'
        self.mission.markers_file = rel_path
        self.mission.save(update_fields=['markers_file'])
        self.assertFalse(markers_abs_path(rel_path).is_file())

        call_command('extract_mission_markers', '--repair-missing')

        self.mission.refresh_from_db()
        self.assertEqual(self.mission.markers_file, rel_path)
        self.assertTrue(markers_abs_path(rel_path).is_file())
        self.assertEqual(len(load_markers_from_mission(self.mission)), 1)


class ReextractMissionsFromPboCommandTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='reextract', password='pass')
        MapName.objects.create(code_name='altis', display_name='Altis')
        self.media_dir = tempfile.mkdtemp()
        self.pbo_dir = tempfile.mkdtemp()
        self.override = override_settings(
            MEDIA_ROOT=self.media_dir,
            MISSIONS_IMAGES_STORAGE_PATH='missions/loadscreens',
            MISSIONS_MARKERS_STORAGE_PATH='missions/markers',
            MISSIONS_PBO_STORAGE_PATH=self.pbo_dir,
        )
        self.override.enable()
        default_storage.location = self.media_dir
        self.mission = Mission.objects.create(
            name='CPC-CO[20]-Reextract',
            user=self.user,
            authors='Old',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
            briefing=[{'name': 'Old', 'content': 'ancien'}],
        )

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media_dir, ignore_errors=True)
        shutil.rmtree(self.pbo_dir, ignore_errors=True)

    def _write_pbo(self):
        pbo_name = mission_pbo_candidate_names(self.mission)[0]
        path = os.path.join(self.pbo_dir, pbo_name)
        with open(path, 'wb') as f:
            f.write(b'placeholder')
        return path

    @patch('gdc_storm.views.extract_briefing_from_pbo')
    @patch('gdc_storm.views.extract_mission_data_from_pbo')
    @patch('yapbol.PBOFile.read_file')
    def test_command_refreshes_briefing_and_markers(
        self, mock_read, mock_data, mock_briefing
    ):
        mock_read.return_value = {'mission.sqm': type('E', (), {'data': SQM_WITH_MARKER})()}
        mock_data.return_value = (
            {
                'author': 'NewAuthor',
                'onLoadMission': 'OL',
                'overviewText': 'OV',
                'loadScreen': None,
                'minPlayers': '8',
            },
            [],
        )
        mock_briefing.return_value = (
            [{'name': 'Situation', 'content': "<a class='briefing-marker-link' data-marker-name='checkpoint_east'>CP</a>"}],
            [],
        )
        self._write_pbo()
        call_command('reextract_missions_from_pbo', '--mission-id', str(self.mission.id))
        self.mission.refresh_from_db()
        self.assertEqual(self.mission.briefing[0]['name'], 'Situation')
        self.assertIn('briefing-marker-link', self.mission.briefing[0]['content'])
        self.assertTrue(self.mission.markers_file)
        self.assertEqual(len(load_markers_from_mission(self.mission)), 1)

    @patch('yapbol.PBOFile.read_file')
    def test_command_dry_run_does_not_write(self, mock_read):
        mock_read.return_value = {'mission.sqm': type('E', (), {'data': SQM_WITH_MARKER})()}
        self._write_pbo()
        old_briefing = self.mission.briefing
        call_command('reextract_missions_from_pbo', '--dry-run', '--markers-only')
        self.mission.refresh_from_db()
        self.assertEqual(self.mission.markers_file, '')
        self.assertEqual(self.mission.briefing, old_briefing)
