"""Tests de non-régression des correctifs d'audit (AUTH, validation, API)."""
import json
import os
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import Group, User
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from gdc_storm.models import ApiToken, Mission, Player
from gdc_storm.views import update_mission_from_pbo, get_upload_temp_dir


class AuditAuthRegressionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.owner = User.objects.create_user('owner', 'o@o.com', 'pass')
        self.outsider = User.objects.create_user('outsider', 'x@x.com', 'pass')
        self.mm = User.objects.create_user('mm', 'm@m.com', 'pass')
        group, _ = Group.objects.get_or_create(name='Mission Maker')
        self.mm.groups.add(group)
        self.mission = Mission.objects.create(
            name='CPC-CO[20]-AuditAuth',
            user=self.owner,
            authors='Auteur',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
        )
        self.temp_dir = get_upload_temp_dir()
        self.temp_path = os.path.join(self.temp_dir, 'audit_auth.pbo')
        with open(self.temp_path, 'wb') as f:
            f.write(b'fake')

    def tearDown(self):
        if os.path.isfile(self.temp_path):
            os.remove(self.temp_path)

    def test_preserve_owner_blocks_non_mission_maker(self):
        request = self.factory.post('/recup/commit/')
        request.user = self.outsider
        mission, msg = update_mission_from_pbo(
            request,
            self.mission,
            self.temp_path,
            'CPC-CO[20]-AuditAuth-V2.altis.pbo',
            'CO',
            20,
            'V2',
            'altis',
            strict=False,
            preserve_owner=True,
        )
        self.assertIsNone(mission)
        self.assertIn('droit', (msg or '').lower())
        self.mission.refresh_from_db()
        self.assertEqual(self.mission.version, '1')

    @patch('gdc_storm.views.save_pbo_to_storage')
    @patch('gdc_storm.views.backup_existing_pbo')
    @patch('gdc_storm.views.extract_briefing_from_pbo')
    @patch('gdc_storm.views.extract_mission_data_from_pbo')
    @patch('gdc_storm.views.is_sqm_binarized', return_value=False)
    @patch('gdc_storm.views.PBOFile.read_file')
    def test_preserve_owner_allows_mission_maker(
        self, mock_read, _bin, mock_extract, mock_briefing, mock_backup, mock_save
    ):
        mock_read.return_value = MagicMock()
        mock_extract.return_value = (
            {
                'author': 'MM',
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
        request.user = self.mm
        mission, _msg = update_mission_from_pbo(
            request,
            self.mission,
            self.temp_path,
            'CPC-CO[20]-AuditAuth-V2.altis.pbo',
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


class AuditValidationTests(TestCase):
    def test_mission_name_accepts_u_circumflex(self):
        m = Mission(
            name='CPC-CO[10]-Flûte_test',
            user=None,
            authors='A',
            max_players=10,
            type='CO',
            version='1',
            map='altis',
        )
        m.save()
        self.assertEqual(Mission.objects.get(pk=m.pk).name, 'CPC-CO[10]-Flûte_test')

    def test_player_name_unique(self):
        Player.objects.create(name='UniquePlayer')
        with self.assertRaises(Exception):
            Player.objects.create(name='UniquePlayer')


class MissionLastStatusUpdateTests(TestCase):
    def test_last_status_update_persists_with_update_fields_status(self):
        from datetime import timedelta
        from django.utils import timezone

        m = Mission.objects.create(
            name='CPC-CO[10]-StatusStamp',
            authors='A',
            max_players=10,
            type='CO',
            version='1',
            map='altis',
            status=Mission.STATUS_JOUABLE,
        )
        old_ts = timezone.now() - timedelta(days=5)
        Mission.objects.filter(pk=m.pk).update(last_status_update=old_ts)
        m.refresh_from_db()

        m.status = Mission.STATUS_NON_JOUABLE
        m.save(update_fields=['status'])
        m.refresh_from_db()

        self.assertEqual(m.status, Mission.STATUS_NON_JOUABLE)
        self.assertIsNotNone(m.last_status_update)
        self.assertGreater(m.last_status_update, old_ts)

    def test_last_status_update_unchanged_when_status_same(self):
        from datetime import timedelta
        from django.utils import timezone

        m = Mission.objects.create(
            name='CPC-CO[10]-StatusSame',
            authors='A',
            max_players=10,
            type='CO',
            version='1',
            map='altis',
            status=Mission.STATUS_JOUABLE,
        )
        old_ts = timezone.now() - timedelta(days=3)
        Mission.objects.filter(pk=m.pk).update(last_status_update=old_ts)
        m.refresh_from_db()

        m.status = Mission.STATUS_JOUABLE
        m.save(update_fields=['status'])
        m.refresh_from_db()

        self.assertEqual(m.last_status_update, old_ts)


class AuditApiValidationTests(TestCase):
    def setUp(self):
        ApiToken.objects.create(key='audit-token', name='audit', is_active=True)
        self.client = Client()

    def test_create_gamesession_requires_mission_name_and_map(self):
        resp = self.client.post(
            reverse('api_create_gamesession'),
            data=json.dumps({'start_time': 1710000000}),
            content_type='application/json',
            HTTP_AUTHORIZATION='audit-token',
        )
        self.assertEqual(resp.status_code, 400)

    def test_api_token_rejected_from_query_string(self):
        resp = self.client.post(
            reverse('api_create_gamesession') + '?api_token=audit-token',
            data=json.dumps({
                'mission_name': 'CPC-CO[10]-X',
                'map': 'altis',
                'start_time': 1710000000,
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 401)
