import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, RequestFactory, override_settings
from django.urls import reverse

from gdc_storm.models import Mission, MapName
from gdc_storm.views import (
    clear_mission_pbo_missing,
    create_mission_from_pbo,
    find_newer_pbo_matches,
    find_orphan_pbo_files,
    scan_missions_pbo_presence,
    update_mission_from_pbo,
)


class ScanMissionsPboPresenceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pass')
        MapName.objects.create(code_name='altis', display_name='Altis')
        self.storage = tempfile.mkdtemp(prefix='gdc_pbo_scan_')
        self.addCleanup(shutil.rmtree, self.storage, ignore_errors=True)
        self.missing = Mission.objects.create(
            name='CPC-CO[20]-MissingPbo',
            user=self.user,
            authors='Auteur',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
            status=Mission.STATUS_JOUABLE,
        )
        self.present = Mission.objects.create(
            name='CPC-CO[20]-PresentPbo',
            user=self.user,
            authors='Auteur',
            max_players=20,
            type='CO',
            version='2',
            map='altis',
            status=Mission.STATUS_JOUABLE,
            pbo_missing=True,
        )
        pbo_path = os.path.join(self.storage, 'CPC-CO[20]-PresentPbo-V2.altis.pbo')
        with open(pbo_path, 'wb') as f:
            f.write(b'pbo')

    def test_scan_marks_missing_and_clears_present(self):
        with override_settings(MISSIONS_PBO_STORAGE_PATH=self.storage):
            summary = scan_missions_pbo_presence()

        self.missing.refresh_from_db()
        self.present.refresh_from_db()
        self.assertTrue(self.missing.pbo_missing)
        self.assertFalse(self.present.pbo_missing)
        self.assertEqual(summary['total'], 2)
        self.assertEqual(summary['missing_count'], 1)
        self.assertEqual(summary['ok_count'], 1)
        self.assertEqual(summary['updated_count'], 2)
        self.assertEqual(len(summary['missing_missions']), 1)
        self.assertEqual(summary['missing_missions'][0]['id'], self.missing.id)

    def test_scan_does_not_change_status(self):
        with override_settings(MISSIONS_PBO_STORAGE_PATH=self.storage):
            scan_missions_pbo_presence()
        self.missing.refresh_from_db()
        self.assertEqual(self.missing.status, Mission.STATUS_JOUABLE)

    def test_scan_detects_newer_pbo_on_disk(self):
        newer = 'CPC-CO[20]-MissingPbo-V5.altis.pbo'
        with open(os.path.join(self.storage, newer), 'wb') as f:
            f.write(b'newer')
        with override_settings(MISSIONS_PBO_STORAGE_PATH=self.storage):
            summary = scan_missions_pbo_presence()
        self.missing.refresh_from_db()
        self.assertTrue(self.missing.pbo_missing)
        self.assertEqual(summary['upgrade_count'], 1)
        self.assertEqual(len(summary['missing_missions']), 0)
        self.assertEqual(summary['upgrade_proposals'][0]['mission_id'], self.missing.id)
        self.assertEqual(summary['upgrade_proposals'][0]['new_version'], '5')
        self.assertEqual(summary['upgrade_proposals'][0]['old_version'], '1')

    def test_scan_matches_map_case_insensitive(self):
        """Linux: fichier Altis.pbo doit matcher map DB altis."""
        Mission.objects.filter(pk=self.missing.pk).delete()
        mission = Mission.objects.create(
            name='CPC-COM[33]-Cache_cash',
            user=self.user,
            authors='Auteur',
            max_players=33,
            type='COM',
            version='35',
            map='altis',
            status=Mission.STATUS_INCONNU,
        )
        with open(os.path.join(self.storage, 'CPC-COM[33]-Cache_cash-V35.Altis.pbo'), 'wb') as f:
            f.write(b'pbo')
        with override_settings(MISSIONS_PBO_STORAGE_PATH=self.storage):
            summary = scan_missions_pbo_presence()
        mission.refresh_from_db()
        self.assertFalse(mission.pbo_missing)
        self.assertNotIn(mission.id, [m['id'] for m in summary['missing_missions']])
        self.assertGreaterEqual(summary['files_indexed'], 2)

    def test_scan_lists_orphan_pbos(self):
        orphan_name = 'CPC-CO[20]-OrphanOnly-V1.altis.pbo'
        with open(os.path.join(self.storage, orphan_name), 'wb') as f:
            f.write(b'orphan')
        with override_settings(MISSIONS_PBO_STORAGE_PATH=self.storage):
            summary = scan_missions_pbo_presence()
        filenames = [o['filename'] for o in summary['orphan_pbos']]
        self.assertIn(orphan_name, filenames)
        self.assertGreaterEqual(summary['orphan_count'], 1)


class FindOrphanPboFilesTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pass')
        MapName.objects.create(code_name='altis', display_name='Altis')
        self.storage = tempfile.mkdtemp(prefix='gdc_pbo_orphan_')
        self.addCleanup(shutil.rmtree, self.storage, ignore_errors=True)
        Mission.objects.create(
            name='CPC-CO[20]-Linked',
            user=self.user,
            authors='A',
            max_players=20,
            type='CO',
            version='2',
            map='altis',
        )

    def test_linked_file_not_orphan(self):
        with open(os.path.join(self.storage, 'CPC-CO[20]-Linked-V2.Altis.pbo'), 'wb') as f:
            f.write(b'x')
        with override_settings(MISSIONS_PBO_STORAGE_PATH=self.storage):
            orphans = find_orphan_pbo_files()
        self.assertEqual(orphans, [])

    def test_unlinked_file_is_orphan(self):
        with open(os.path.join(self.storage, 'CPC-CO[20]-Ghost-V1.altis.pbo'), 'wb') as f:
            f.write(b'x')
        with override_settings(MISSIONS_PBO_STORAGE_PATH=self.storage):
            orphans = find_orphan_pbo_files()
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0]['name'], 'CPC-CO[20]-Ghost')
        self.assertIsNone(orphans[0]['related_mission_id'])

    def test_different_version_notes_related_mission(self):
        with open(os.path.join(self.storage, 'CPC-CO[20]-Linked-V9.altis.pbo'), 'wb') as f:
            f.write(b'x')
        with override_settings(MISSIONS_PBO_STORAGE_PATH=self.storage):
            orphans = find_orphan_pbo_files()
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0]['related_version'], '2')

    def test_mare_aux_canards_ignored(self):
        with open(
            os.path.join(self.storage, 'CPC-CO[20]-Mare_aux_canards-V1.altis.pbo'),
            'wb',
        ) as f:
            f.write(b'x')
        with open(
            os.path.join(self.storage, 'CPC-TRAINING[10]-Mare_aux_canards_RHS-V2.tanoa.pbo'),
            'wb',
        ) as f:
            f.write(b'x')
        with override_settings(MISSIONS_PBO_STORAGE_PATH=self.storage):
            orphans = find_orphan_pbo_files()
        self.assertEqual(orphans, [])


class FindNewerPboMatchesTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pass')
        MapName.objects.create(code_name='altis', display_name='Altis')
        self.storage = tempfile.mkdtemp(prefix='gdc_pbo_newer_')
        self.addCleanup(shutil.rmtree, self.storage, ignore_errors=True)
        self.mission = Mission.objects.create(
            name='CPC-CO[20]-UpgradeMe',
            user=self.user,
            authors='Auteur',
            max_players=20,
            type='CO',
            version='2',
            map='altis',
            pbo_missing=True,
        )

    def test_picks_highest_newer_version(self):
        for name in (
            'CPC-CO[20]-UpgradeMe-V1.altis.pbo',
            'CPC-CO[20]-UpgradeMe-V3.altis.pbo',
            'CPC-CO[20]-UpgradeMe-V4.altis.pbo',
        ):
            with open(os.path.join(self.storage, name), 'wb') as f:
                f.write(b'x')
        with override_settings(MISSIONS_PBO_STORAGE_PATH=self.storage):
            proposals = find_newer_pbo_matches([self.mission])
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]['new_version'], '4')
        self.assertEqual(proposals[0]['filename'], 'CPC-CO[20]-UpgradeMe-V4.altis.pbo')

    def test_ignores_same_or_older_version(self):
        with open(os.path.join(self.storage, 'CPC-CO[20]-UpgradeMe-V2.altis.pbo'), 'wb') as f:
            f.write(b'x')
        with open(os.path.join(self.storage, 'CPC-CO[20]-UpgradeMe-V1.altis.pbo'), 'wb') as f:
            f.write(b'x')
        with override_settings(MISSIONS_PBO_STORAGE_PATH=self.storage):
            proposals = find_newer_pbo_matches([self.mission])
        self.assertEqual(proposals, [])


class ScanPboMissingApplyTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.superuser = User.objects.create_superuser(
            username='admin', password='pass', email='a@b.c',
        )
        self.user = User.objects.create_user(username='user', password='pass')
        MapName.objects.create(code_name='altis', display_name='Altis')
        self.storage = tempfile.mkdtemp(prefix='gdc_pbo_apply_')
        self.addCleanup(shutil.rmtree, self.storage, ignore_errors=True)
        self.mission = Mission.objects.create(
            name='CPC-CO[20]-ApplyUpgrade',
            user=self.user,
            authors='Old',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
            pbo_missing=True,
        )
        self.filename = 'CPC-CO[20]-ApplyUpgrade-V3.altis.pbo'
        with open(os.path.join(self.storage, self.filename), 'wb') as f:
            f.write(b'fake-pbo')

    def _mock_pbo(self):
        pbo = MagicMock()
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
        entry = MagicMock(data=sqm)
        pbo.__getitem__.side_effect = lambda key: entry if key == 'mission.sqm' else (_ for _ in ()).throw(KeyError(key))
        return pbo

    def test_apply_forbidden_for_non_superuser(self):
        self.client.login(username='user', password='pass')
        resp = self.client.post(
            reverse('scan_pbo_missing_apply'),
            data='{"mission_id": %d, "filename": "%s"}' % (self.mission.id, self.filename),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403)

    @patch('gdc_storm.views.is_sqm_binarized', return_value=False)
    @patch('gdc_storm.views.extract_briefing_from_pbo')
    @patch('gdc_storm.views.extract_mission_data_from_pbo')
    @patch('gdc_storm.views.PBOFile.read_file')
    def test_apply_upgrades_mission(
        self, mock_read, mock_extract, mock_briefing, _bin,
    ):
        mock_read.return_value = self._mock_pbo()
        mock_extract.return_value = (
            {
                'author': 'NewAuthor',
                'onLoadMission': 'x',
                'overviewText': 'y',
                'loadScreen': None,
                'minPlayers': '2',
            },
            [],
        )
        mock_briefing.return_value = ([], [])
        self.client.login(username='admin', password='pass')
        with override_settings(MISSIONS_PBO_STORAGE_PATH=self.storage):
            resp = self.client.post(
                reverse('scan_pbo_missing_apply'),
                data='{"mission_id": %d, "filename": "%s"}' % (self.mission.id, self.filename),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.mission.refresh_from_db()
        self.assertEqual(self.mission.version, '3')
        self.assertFalse(self.mission.pbo_missing)
        self.assertEqual(self.mission.user, self.user)
        self.assertTrue(os.path.isfile(os.path.join(self.storage, self.filename)))


class ScanPboMissingViewsTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.superuser = User.objects.create_superuser(
            username='admin', password='pass', email='a@b.c',
        )
        self.user = User.objects.create_user(username='user', password='pass')
        MapName.objects.create(code_name='altis', display_name='Altis')
        Mission.objects.create(
            name='CPC-CO[20]-ScanView',
            user=self.user,
            authors='Auteur',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
            pbo_missing=True,
        )

    def test_page_requires_login(self):
        resp = self.client.get(reverse('scan_pbo_missing'))
        self.assertEqual(resp.status_code, 302)

    def test_page_forbidden_for_non_superuser(self):
        self.client.login(username='user', password='pass')
        resp = self.client.get(reverse('scan_pbo_missing'))
        self.assertEqual(resp.status_code, 403)

    def test_page_ok_for_superuser(self):
        self.client.login(username='admin', password='pass')
        resp = self.client.get(reverse('scan_pbo_missing'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'CPC-CO[20]-ScanView')
        self.assertContains(resp, 'PBO manquant')

    def test_run_forbidden_for_non_superuser(self):
        self.client.login(username='user', password='pass')
        resp = self.client.post(reverse('scan_pbo_missing_run'))
        self.assertEqual(resp.status_code, 403)

    def test_run_ok_for_superuser(self):
        storage = tempfile.mkdtemp(prefix='gdc_pbo_run_')
        self.addCleanup(shutil.rmtree, storage, ignore_errors=True)
        self.client.login(username='admin', password='pass')
        with override_settings(MISSIONS_PBO_STORAGE_PATH=storage):
            resp = self.client.post(reverse('scan_pbo_missing_run'))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['missing_count'], 1)


class ClearMissionPboMissingTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.owner = User.objects.create_user(username='owner', password='pass')
        MapName.objects.create(code_name='altis', display_name='Altis')
        self.mission = Mission.objects.create(
            name='CPC-CO[20]-ClearFlag',
            user=self.owner,
            authors='Auteur',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
            pbo_missing=True,
        )
        self.temp_path = tempfile.NamedTemporaryFile(delete=False, suffix='.pbo').name
        with open(self.temp_path, 'wb') as f:
            f.write(b'fake')
        self.addCleanup(lambda: os.path.exists(self.temp_path) and os.unlink(self.temp_path))

    def _mock_pbo(self):
        pbo = MagicMock()
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
        entry = MagicMock(data=sqm)
        pbo.__getitem__.side_effect = lambda key: entry if key == 'mission.sqm' else (_ for _ in ()).throw(KeyError(key))
        return pbo

    def test_clear_helper(self):
        clear_mission_pbo_missing(self.mission)
        self.mission.refresh_from_db()
        self.assertFalse(self.mission.pbo_missing)

    @patch('gdc_storm.views.is_sqm_binarized', return_value=False)
    @patch('gdc_storm.views.extract_briefing_from_pbo')
    @patch('gdc_storm.views.extract_mission_data_from_pbo')
    @patch('gdc_storm.views.PBOFile.read_file')
    def test_create_clears_flag_after_pbo_save(
        self, mock_read, mock_extract, mock_briefing, _bin,
    ):
        mock_read.return_value = self._mock_pbo()
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
        request = self.factory.post('/')
        request.user = self.owner
        storage = tempfile.mkdtemp(prefix='gdc_create_clear_')
        self.addCleanup(shutil.rmtree, storage, ignore_errors=True)

        with override_settings(MISSIONS_PBO_STORAGE_PATH=storage):
            mission, err = create_mission_from_pbo(
                request,
                self.temp_path,
                'CPC-CO[20]-NewClear-V1.altis.pbo',
                'CPC-CO[20]-NewClear',
                'CO',
                20,
                'V1',
                'altis',
            )
        self.assertIsNotNone(mission)
        self.assertIsNone(err)
        mission.refresh_from_db()
        self.assertFalse(mission.pbo_missing)

    @patch('gdc_storm.views.is_sqm_binarized', return_value=False)
    @patch('gdc_storm.views.extract_briefing_from_pbo')
    @patch('gdc_storm.views.extract_mission_data_from_pbo')
    @patch('gdc_storm.views.PBOFile.read_file')
    def test_update_clears_flag_after_pbo_save(
        self, mock_read, mock_extract, mock_briefing, _bin,
    ):
        mock_read.return_value = self._mock_pbo()
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
        request = self.factory.post('/')
        request.user = self.owner
        storage = tempfile.mkdtemp(prefix='gdc_upd_clear_')
        self.addCleanup(shutil.rmtree, storage, ignore_errors=True)
        # recreate temp since create may have moved it
        with open(self.temp_path, 'wb') as f:
            f.write(b'fake')

        with override_settings(MISSIONS_PBO_STORAGE_PATH=storage):
            mission, err = update_mission_from_pbo(
                request,
                self.mission,
                self.temp_path,
                'CPC-CO[20]-ClearFlag-V1.altis.pbo',
                'CO',
                20,
                'V1',
                'altis',
            )
        self.assertIsNotNone(mission)
        self.assertIsNone(err)
        self.mission.refresh_from_db()
        self.assertFalse(self.mission.pbo_missing)
