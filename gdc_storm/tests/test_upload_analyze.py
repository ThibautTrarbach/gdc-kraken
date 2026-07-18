import json
import os
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from gdc_storm.models import Mission, MapName
from gdc_storm.views import (
    analyze_pbo_upload,
    get_upload_temp_dir,
    is_safe_upload_temp_path,
    UPLOAD_ANALYZE_MAX_FILES,
)


class AnalyzePboUploadTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='maker', password='pass')
        MapName.objects.create(code_name='altis', display_name='Altis')
        MapName.objects.create(code_name='malden', display_name='Malden')

    def _create_mission(self, name='CPC-CO[20]-TestMission', map_code='altis', version='1', max_players=20):
        return Mission.objects.create(
            name=name,
            user=self.user,
            authors='Auteur',
            max_players=max_players,
            type='CO',
            version=version,
            map=map_code,
        )

    def test_invalid_filename(self):
        result = analyze_pbo_upload('badname.pbo')
        self.assertEqual(result['action'], 'error')
        self.assertIn('invalide', result['error'].lower())

    def test_create_new_mission(self):
        result = analyze_pbo_upload('CPC-CO[20]-NewMission-V1.altis.pbo')
        self.assertEqual(result['action'], 'create')
        self.assertIsNone(result['error'])
        self.assertEqual(result['details']['mission_name'], 'CPC-CO[20]-NewMission')
        self.assertEqual(result['details']['version'], '1')
        self.assertEqual(result['details']['map'], 'altis')

    def test_update_when_newer_version(self):
        self._create_mission(version='1')
        result = analyze_pbo_upload('CPC-CO[20]-TestMission-V2.altis.pbo')
        self.assertEqual(result['action'], 'update')
        self.assertEqual(result['details']['old_version'], '1')
        self.assertEqual(result['details']['new_version'], '2')

    def test_update_when_version_is_two_digits(self):
        """Régression : comparaison string faisait \"10\" < \"7\"."""
        self._create_mission(
            name='CPC-CO[09]-Dingor-Insurection-M01',
            map_code='dingor',
            version='7',
            max_players=9,
        )
        MapName.objects.get_or_create(code_name='dingor', defaults={'display_name': 'Dingor'})
        result = analyze_pbo_upload('CPC-CO[09]-Dingor-Insurection-M01-v10.dingor.pbo')
        self.assertEqual(result['action'], 'update')
        self.assertEqual(result['details']['old_version'], '7')
        self.assertEqual(result['details']['new_version'], '10')

    def test_error_when_version_not_newer(self):
        self._create_mission(version='3')
        result = analyze_pbo_upload('CPC-CO[20]-TestMission-V2.altis.pbo')
        self.assertEqual(result['action'], 'error')
        self.assertIn('supérieure ou égale', result['error'])

    def test_create_duplicate_same_name_different_map(self):
        self._create_mission(map_code='altis')
        result = analyze_pbo_upload('CPC-CO[20]-TestMission-V1.malden.pbo')
        self.assertEqual(result['action'], 'create_duplicate')
        self.assertEqual(len(result['details']['duplicates']), 1)
        self.assertEqual(result['details']['map'], 'malden')


class UploadTempPathSafetyTest(TestCase):
    def test_rejects_path_outside_temp(self):
        self.assertFalse(is_safe_upload_temp_path('/etc/passwd'))
        self.assertFalse(is_safe_upload_temp_path(''))

    def test_accepts_existing_file_in_temp(self):
        temp_dir = get_upload_temp_dir()
        path = os.path.join(temp_dir, 'safety_test.pbo')
        with open(path, 'wb') as f:
            f.write(b'x')
        try:
            self.assertTrue(is_safe_upload_temp_path(path))
        finally:
            os.remove(path)


class UploadEndpointsTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='maker', password='pass')
        group, _ = Group.objects.get_or_create(name='Mission Maker')
        self.user.groups.add(group)
        self.client.login(username='maker', password='pass')
        MapName.objects.create(code_name='altis', display_name='Altis')

    def test_analyze_requires_mission_maker(self):
        other = User.objects.create_user(username='plain', password='pass')
        self.client.logout()
        self.client.login(username='plain', password='pass')
        resp = self.client.post(reverse('upload_analyze'))
        self.assertEqual(resp.status_code, 403)

    def test_analyze_create_action(self):
        uploaded = SimpleUploadedFile(
            'CPC-CO[20]-FreshMission-V1.altis.pbo',
            b'fake-pbo-content',
            content_type='application/octet-stream',
        )
        resp = self.client.post(reverse('upload_analyze'), {'pbo_files': uploaded})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(len(data['items']), 1)
        item = data['items'][0]
        self.assertEqual(item['action'], 'create')
        self.assertTrue(os.path.isfile(item['temp_file_path']))
        os.remove(item['temp_file_path'])

    def test_analyze_rejects_too_many_files(self):
        files = [
            SimpleUploadedFile(
                f'CPC-CO[20]-M{i}-V1.altis.pbo',
                b'x',
                content_type='application/octet-stream',
            )
            for i in range(UPLOAD_ANALYZE_MAX_FILES + 1)
        ]
        resp = self.client.post(reverse('upload_analyze'), {'pbo_files': files})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Trop de fichiers', resp.json()['error'])

    @patch('gdc_storm.views.create_mission_from_pbo')
    def test_commit_create(self, mock_create):
        mission = Mission(
            id=42,
            name='CPC-CO[20]-FreshMission',
            user=self.user,
            authors='A',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
        )
        mock_create.return_value = (mission, None)

        temp_dir = get_upload_temp_dir()
        filename = 'CPC-CO[20]-FreshMission-V1.altis.pbo'
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
            reverse('upload_commit'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['success_count'], 1)
        self.assertEqual(data['fail_count'], 0)
        self.assertTrue(data['results'][0]['success'])
        mock_create.assert_called_once()

    def test_commit_update_requires_confirm(self):
        Mission.objects.create(
            name='CPC-CO[20]-TestMission',
            user=self.user,
            authors='Auteur',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
        )
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
                'confirm_update': False,
            }]
        }
        resp = self.client.post(
            reverse('upload_commit'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        data = resp.json()
        self.assertEqual(data['fail_count'], 1)
        self.assertIn('Confirmation', data['results'][0]['error'])
        os.remove(temp_file_path)

    def test_upload_page_get(self):
        resp = self.client.get(reverse('upload_mission'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Analyser')
        self.assertContains(resp, 'pbo_files')
