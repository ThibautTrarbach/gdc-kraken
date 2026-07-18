import os
import tempfile
import shutil

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from gdc_storm.models import Mission
from gdc_storm.views import backup_existing_pbo


class BackupExistingPboTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='maker', password='pass')
        self.storage = tempfile.mkdtemp(prefix='gdc_pbo_')
        self.addCleanup(shutil.rmtree, self.storage, ignore_errors=True)
        self.mission = Mission.objects.create(
            name='CPC-CO[09]-Dingor-Insurection-M01',
            user=self.user,
            authors='Auteur',
            max_players=9,
            type='CO',
            version='8',
            map='dingor',
        )

    def test_moves_old_pbo_to_backup(self):
        old_name = 'CPC-CO[09]-Dingor-Insurection-M01-V8.dingor.pbo'
        old_path = os.path.join(self.storage, old_name)
        with open(old_path, 'wb') as f:
            f.write(b'old-pbo')

        with override_settings(MISSIONS_PBO_STORAGE_PATH=self.storage):
            dest = backup_existing_pbo(self.mission)

        self.assertIsNotNone(dest)
        self.assertFalse(os.path.exists(old_path))
        self.assertTrue(os.path.isfile(dest))
        self.assertEqual(os.path.basename(dest), old_name)
        self.assertEqual(
            os.path.normpath(os.path.dirname(dest)),
            os.path.normpath(os.path.join(self.storage, 'backup')),
        )

    def test_finds_lowercase_v_prefix(self):
        old_name = 'CPC-CO[09]-Dingor-Insurection-M01-v8.dingor.pbo'
        old_path = os.path.join(self.storage, old_name)
        with open(old_path, 'wb') as f:
            f.write(b'old-pbo')

        with override_settings(MISSIONS_PBO_STORAGE_PATH=self.storage):
            dest = backup_existing_pbo(self.mission)

        self.assertIsNotNone(dest)
        self.assertFalse(os.path.exists(old_path))
        self.assertTrue(os.path.isfile(dest))

    def test_collision_in_backup_gets_timestamp_suffix(self):
        old_name = 'CPC-CO[09]-Dingor-Insurection-M01-V8.dingor.pbo'
        old_path = os.path.join(self.storage, old_name)
        backup_dir = os.path.join(self.storage, 'backup')
        os.makedirs(backup_dir)
        existing_backup = os.path.join(backup_dir, old_name)
        with open(old_path, 'wb') as f:
            f.write(b'new-old')
        with open(existing_backup, 'wb') as f:
            f.write(b'already-backed')

        with override_settings(MISSIONS_PBO_STORAGE_PATH=self.storage):
            dest = backup_existing_pbo(self.mission)

        self.assertIsNotNone(dest)
        self.assertNotEqual(os.path.basename(dest), old_name)
        self.assertIn('_', os.path.basename(dest))
        self.assertTrue(dest.endswith('.pbo'))
        self.assertTrue(os.path.isfile(existing_backup))
        self.assertTrue(os.path.isfile(dest))

    def test_missing_pbo_is_non_blocking(self):
        with override_settings(MISSIONS_PBO_STORAGE_PATH=self.storage):
            dest = backup_existing_pbo(self.mission)
        self.assertIsNone(dest)
        self.assertFalse(os.path.isdir(os.path.join(self.storage, 'backup')))
