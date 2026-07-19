import json
import os
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User, Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, RequestFactory
from django.urls import reverse

from gdc_storm.models import Mission, MapName
from gdc_storm.views import (
    RECUP_USERNAME,
    UPLOAD_ANALYZE_MAX_FILES,
    analyze_pbo_upload,
    create_mission_from_pbo,
    get_recup_user,
    get_upload_temp_dir,
    update_mission_from_pbo,
)


def _make_mission_maker(user):
    group, _ = Group.objects.get_or_create(name='Mission Maker')
    user.groups.add(group)
    return user


class GetRecupUserTest(TestCase):
    def test_creates_inactive_user(self):
        user = get_recup_user()
        self.assertEqual(user.username, RECUP_USERNAME)
        self.assertFalse(user.is_active)
        self.assertFalse(user.has_usable_password())

    def test_idempotent(self):
        u1 = get_recup_user()
        u2 = get_recup_user()
        self.assertEqual(u1.id, u2.id)
        self.assertEqual(User.objects.filter(username=RECUP_USERNAME).count(), 1)


class AnalyzeRestoreModeTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pass')
        MapName.objects.create(code_name='altis', display_name='Altis')
        Mission.objects.create(
            name='CPC-CO[20]-TestMission',
            user=self.user,
            authors='Auteur',
            max_players=20,
            type='CO',
            version='3',
            map='altis',
        )

    def test_normal_mode_rejects_same_or_older_version(self):
        result = analyze_pbo_upload('CPC-CO[20]-TestMission-V3.altis.pbo')
        self.assertEqual(result['action'], 'error')

    def test_restore_mode_accepts_same_version(self):
        result = analyze_pbo_upload(
            'CPC-CO[20]-TestMission-V3.altis.pbo',
            allow_pbo_restore=True,
        )
        self.assertEqual(result['action'], 'update')
        self.assertTrue(result['details']['restore'])
        self.assertTrue(result['details']['pbo_missing'])
        self.assertEqual(result['details']['old_version'], '3')

    def test_restore_mode_rejects_when_pbo_exists(self):
        from django.test import override_settings
        import tempfile
        import shutil
        storage = tempfile.mkdtemp(prefix='gdc_pbo_')
        self.addCleanup(shutil.rmtree, storage, ignore_errors=True)
        pbo_name = 'CPC-CO[20]-TestMission-V3.altis.pbo'
        with open(os.path.join(storage, pbo_name), 'wb') as f:
            f.write(b'existing')
        with override_settings(MISSIONS_PBO_STORAGE_PATH=storage):
            result = analyze_pbo_upload(pbo_name, allow_pbo_restore=True)
        self.assertEqual(result['action'], 'error')
        self.assertIn('remplacement non autorisé', result['error'].lower())

    def test_restore_mode_accepts_older_version(self):
        result = analyze_pbo_upload(
            'CPC-CO[20]-TestMission-V1.altis.pbo',
            allow_pbo_restore=True,
        )
        self.assertEqual(result['action'], 'update')
        self.assertTrue(result['details']['restore'])
        self.assertEqual(result['details']['new_version'], '1')

    def test_restore_mode_newer_version_is_update_not_restore(self):
        result = analyze_pbo_upload(
            'CPC-CO[20]-TestMission-V5.altis.pbo',
            allow_pbo_restore=True,
        )
        self.assertEqual(result['action'], 'update')
        self.assertFalse(result['details']['restore'])
        self.assertEqual(result['details']['new_version'], '5')


class SoftCreateUpdateTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.owner = User.objects.create_user(username='owner', password='pass')
        self.uploader = _make_mission_maker(
            User.objects.create_user(username='uploader', password='pass')
        )
        self.recup = get_recup_user()
        MapName.objects.create(code_name='altis', display_name='Altis')
        self.temp_dir = get_upload_temp_dir()
        self.temp_path = os.path.join(self.temp_dir, 'soft_test.pbo')
        with open(self.temp_path, 'wb') as f:
            f.write(b'fake')

    def tearDown(self):
        if os.path.exists(self.temp_path):
            os.remove(self.temp_path)

    def _mock_pbo(self, *, binarized=False, has_hc=False, has_sqm=True):
        pbo = MagicMock()
        if not has_sqm:
            def missing(key):
                raise KeyError(key)
            pbo.__getitem__.side_effect = missing
            return pbo

        if binarized:
            sqm = b'binarygarbage\x00\x01'
        elif has_hc:
            sqm = (
                b'version=54;\n'
                b'class Entities {\n'
                b'  class Item0 {\n'
                b'    name="HC_Slot";\n'
                b'    isPlayable=1;\n'
                b'    type="HeadlessClient_F";\n'
                b'  };\n'
                b'};\n'
            )
        else:
            sqm = b'version=54;\nclass Entities {};\n'

        entry = MagicMock(data=sqm)
        pbo.__getitem__.side_effect = lambda key: entry if key == 'mission.sqm' else (_ for _ in ()).throw(KeyError(key))
        return pbo

    @patch('gdc_storm.views.save_pbo_to_storage')
    @patch('gdc_storm.views.extract_briefing_from_pbo')
    @patch('gdc_storm.views.extract_mission_data_from_pbo')
    @patch('gdc_storm.views.PBOFile.read_file')
    def test_soft_create_accepts_no_hc_binarized(
        self, mock_read, mock_extract, mock_briefing, mock_save
    ):
        mock_read.return_value = self._mock_pbo(binarized=True, has_hc=False)
        mock_extract.return_value = (
            {
                'author': None,
                'onLoadMission': None,
                'overviewText': None,
                'loadScreen': None,
                'minPlayers': None,
            },
            ["Champ 'author' non trouvé"],
        )
        mock_briefing.return_value = (None, [])
        mock_save.return_value = None

        request = self.factory.post('/recup/commit/')
        request.user = self.uploader
        mission, msg = create_mission_from_pbo(
            request,
            self.temp_path,
            'CPC-CO[20]-SoftMission-V1.altis.pbo',
            'CPC-CO[20]-SoftMission',
            'CO',
            20,
            'V1',
            'altis',
            strict=False,
            owner_user=self.recup,
        )
        self.assertIsNotNone(mission)
        self.assertEqual(mission.user, self.recup)
        self.assertEqual(mission.name, 'CPC-CO[20]-SoftMission')
        self.assertIsNotNone(msg)  # warnings expected

    @patch('gdc_storm.views.save_pbo_to_storage')
    @patch('gdc_storm.views.extract_briefing_from_pbo')
    @patch('gdc_storm.views.extract_mission_data_from_pbo')
    @patch('gdc_storm.views.PBOFile.read_file')
    def test_strict_create_rejects_no_hc(
        self, mock_read, mock_extract, mock_briefing, mock_save
    ):
        mock_read.return_value = self._mock_pbo(binarized=False, has_hc=False)
        mock_extract.return_value = (
            {
                'author': 'A',
                'onLoadMission': 'x',
                'overviewText': 'y',
                'loadScreen': None,
                'minPlayers': '1',
            },
            [],
        )
        mock_briefing.return_value = ([], [])

        request = self.factory.post('/upload/commit/')
        request.user = self.uploader
        mission, msg = create_mission_from_pbo(
            request,
            self.temp_path,
            'CPC-CO[20]-StrictMission-V1.altis.pbo',
            'CPC-CO[20]-StrictMission',
            'CO',
            20,
            'V1',
            'altis',
            strict=True,
        )
        self.assertIsNone(mission)
        self.assertIn('Headless Client', msg)

    @patch('gdc_storm.views.save_pbo_to_storage')
    @patch('gdc_storm.views.backup_existing_pbo')
    @patch('gdc_storm.views.extract_briefing_from_pbo')
    @patch('gdc_storm.views.extract_mission_data_from_pbo')
    @patch('gdc_storm.views.PBOFile.read_file')
    def test_soft_update_preserves_owner(
        self, mock_read, mock_extract, mock_briefing, mock_backup, mock_save
    ):
        existing = Mission.objects.create(
            name='CPC-CO[20]-OwnedMission',
            user=self.owner,
            authors='Owner',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
        )
        mock_read.return_value = self._mock_pbo(binarized=True, has_hc=False)
        mock_extract.return_value = (
            {
                'author': 'New',
                'onLoadMission': None,
                'overviewText': None,
                'loadScreen': None,
                'minPlayers': None,
            },
            [],
        )
        mock_briefing.return_value = ([], [])
        mock_save.return_value = None

        request = self.factory.post('/recup/commit/')
        request.user = self.uploader
        mission, msg = update_mission_from_pbo(
            request,
            existing,
            self.temp_path,
            'CPC-CO[20]-OwnedMission-V2.altis.pbo',
            'CO',
            20,
            'V2',
            'altis',
            strict=False,
            preserve_owner=True,
        )
        self.assertIsNotNone(mission)
        mission.refresh_from_db()
        self.assertEqual(mission.user_id, self.owner.id)
        self.assertEqual(mission.version, '2')
        self.assertEqual(mission.authors, 'New')

    @patch('gdc_storm.views.save_pbo_to_storage')
    @patch('gdc_storm.views.backup_existing_pbo')
    @patch('gdc_storm.views.extract_briefing_from_pbo')
    @patch('gdc_storm.views.extract_mission_data_from_pbo')
    @patch('gdc_storm.views.PBOFile.read_file')
    def test_preserve_owner_denied_without_mission_maker(
        self, mock_read, mock_extract, mock_briefing, mock_backup, mock_save
    ):
        outsider = User.objects.create_user(username='outsider_upd', password='pass')
        existing = Mission.objects.create(
            name='CPC-CO[20]-DeniedMission',
            user=self.owner,
            authors='Owner',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
        )
        request = self.factory.post('/recup/commit/')
        request.user = outsider
        mission, msg = update_mission_from_pbo(
            request,
            existing,
            self.temp_path,
            'CPC-CO[20]-DeniedMission-V2.altis.pbo',
            'CO',
            20,
            'V2',
            'altis',
            strict=False,
            preserve_owner=True,
        )
        self.assertIsNone(mission)
        self.assertIn('droit', msg.lower())
        existing.refresh_from_db()
        self.assertEqual(existing.version, '1')


class RecupEndpointsTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = _make_mission_maker(
            User.objects.create_user(username='plain', password='pass')
        )
        self.outsider = User.objects.create_user(username='outsider', password='pass')
        MapName.objects.create(code_name='altis', display_name='Altis')
        MapName.objects.create(code_name='malden', display_name='Malden')

    def test_page_requires_login(self):
        resp = self.client.get(reverse('recup_missions'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp.url)

    def test_page_forbidden_without_mission_maker(self):
        self.client.login(username='outsider', password='pass')
        resp = self.client.get(reverse('recup_missions'))
        self.assertEqual(resp.status_code, 403)

    def test_page_ok_for_mission_maker(self):
        self.client.login(username='plain', password='pass')
        resp = self.client.get(reverse('recup_missions'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'MPMissionsCache')
        self.assertContains(resp, 'GDC-RECUP')

    def test_analyze_ok_for_mission_maker(self):
        self.client.login(username='plain', password='pass')
        uploaded = SimpleUploadedFile(
            'CPC-CO[20]-FreshRecup-V1.altis.pbo',
            b'fake-pbo-content',
            content_type='application/octet-stream',
        )
        resp = self.client.post(reverse('recup_analyze'), {'pbo_files': uploaded})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['items'][0]['action'], 'create')
        os.remove(data['items'][0]['temp_file_path'])

    def test_analyze_forbidden_for_plain_user(self):
        self.client.login(username='outsider', password='pass')
        uploaded = SimpleUploadedFile(
            'CPC-CO[20]-FreshRecup-V1.altis.pbo',
            b'fake-pbo-content',
            content_type='application/octet-stream',
        )
        resp = self.client.post(reverse('recup_analyze'), {'pbo_files': uploaded})
        self.assertEqual(resp.status_code, 403)

    def test_analyze_invalid_filename(self):
        self.client.login(username='plain', password='pass')
        uploaded = SimpleUploadedFile(
            'badname.altis.pbo',
            b'x',
            content_type='application/octet-stream',
        )
        resp = self.client.post(reverse('recup_analyze'), {'pbo_files': uploaded})
        self.assertEqual(resp.status_code, 200)
        item = resp.json()['items'][0]
        self.assertEqual(item['action'], 'error')
        self.assertIn('invalide', item['error'].lower())
        if item.get('temp_file_path') and os.path.isfile(item['temp_file_path']):
            os.remove(item['temp_file_path'])

    def test_analyze_missing_version_rejected(self):
        self.client.login(username='plain', password='pass')
        uploaded = SimpleUploadedFile(
            'CPC-CO[20]-NoVersion.altis.pbo',
            b'x',
            content_type='application/octet-stream',
        )
        resp = self.client.post(reverse('recup_analyze'), {'pbo_files': uploaded})
        self.assertEqual(resp.status_code, 200)
        item = resp.json()['items'][0]
        self.assertEqual(item['action'], 'error')
        if item.get('temp_file_path') and os.path.isfile(item['temp_file_path']):
            os.remove(item['temp_file_path'])

    def test_analyze_rejects_too_many_files(self):
        self.client.login(username='plain', password='pass')
        files = [
            SimpleUploadedFile(
                f'CPC-CO[20]-R{i}-V1.altis.pbo',
                b'x',
                content_type='application/octet-stream',
            )
            for i in range(UPLOAD_ANALYZE_MAX_FILES + 1)
        ]
        resp = self.client.post(reverse('recup_analyze'), {'pbo_files': files})
        self.assertEqual(resp.status_code, 400)

    @patch('gdc_storm.views.create_mission_from_pbo')
    def test_commit_create_uses_recup_owner(self, mock_create):
        self.client.login(username='plain', password='pass')
        recup = get_recup_user()
        mission = Mission(
            id=99,
            name='CPC-CO[20]-FreshRecup',
            user=recup,
            authors='A',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
        )
        mock_create.return_value = (mission, None)

        temp_dir = get_upload_temp_dir()
        filename = 'CPC-CO[20]-FreshRecup-V1.altis.pbo'
        temp_file_name = f'testuuid_{filename}'
        temp_file_path = os.path.join(temp_dir, temp_file_name)
        with open(temp_file_path, 'wb') as f:
            f.write(b'fake')

        payload = {
            'items': [{
                'filename': filename,
                'temp_file_path': temp_file_path,
                'temp_file_name': temp_file_name,
                'action': 'create',
                'confirm_publish': False,
                'confirm_update': False,
            }]
        }
        resp = self.client.post(
            reverse('recup_commit'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['success_count'], 1)
        mock_create.assert_called_once()
        kwargs = mock_create.call_args.kwargs
        self.assertEqual(kwargs['strict'], False)
        self.assertEqual(kwargs['owner_user'].username, RECUP_USERNAME)

    @patch('gdc_storm.views.update_mission_from_pbo')
    def test_commit_update_preserve_owner_flag(self, mock_update):
        self.client.login(username='plain', password='pass')
        owner = User.objects.create_user(username='orig', password='pass')
        existing = Mission.objects.create(
            name='CPC-CO[20]-TestMission',
            user=owner,
            authors='Auteur',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
        )
        mock_update.return_value = (existing, None)

        temp_dir = get_upload_temp_dir()
        filename = 'CPC-CO[20]-TestMission-V2.altis.pbo'
        temp_file_name = f'testuuid_{filename}'
        temp_file_path = os.path.join(temp_dir, temp_file_name)
        with open(temp_file_path, 'wb') as f:
            f.write(b'fake')

        payload = {
            'items': [{
                'filename': filename,
                'temp_file_path': temp_file_path,
                'temp_file_name': temp_file_name,
                'action': 'update',
                'confirm_publish': False,
                'confirm_update': True,
            }]
        }
        resp = self.client.post(
            reverse('recup_commit'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['success_count'], 1)
        kwargs = mock_update.call_args.kwargs
        self.assertEqual(kwargs['strict'], False)
        self.assertEqual(kwargs['preserve_owner'], True)

    def test_commit_duplicate_requires_confirm(self):
        self.client.login(username='plain', password='pass')
        Mission.objects.create(
            name='CPC-CO[20]-TestMission',
            user=self.user,
            authors='A',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
        )
        temp_dir = get_upload_temp_dir()
        filename = 'CPC-CO[20]-TestMission-V1.malden.pbo'
        temp_file_name = f'testuuid_{filename}'
        temp_file_path = os.path.join(temp_dir, temp_file_name)
        with open(temp_file_path, 'wb') as f:
            f.write(b'fake')

        payload = {
            'items': [{
                'filename': filename,
                'temp_file_path': temp_file_path,
                'temp_file_name': temp_file_name,
                'action': 'create_duplicate',
                'confirm_publish': False,
                'confirm_update': False,
            }]
        }
        resp = self.client.post(
            reverse('recup_commit'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['fail_count'], 1)
        self.assertIn('publier quand même', data['results'][0]['error'].lower())
        os.remove(temp_file_path)
