"""Tests de non-régression des correctifs de sécurité critiques."""
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import clear_url_caches

from gdc_storm.models import GameSession, Mission
from gdc_storm.views import get_upload_temp_dir, is_safe_upload_temp_path, save_uploaded_pbo_to_temp


class CriticalSecurityRegressionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@a.com', 'pass')
        self.regular = User.objects.create_user('regular', 'r@r.com', 'pass')
        self.owner = User.objects.create_user('owner', 'o@o.com', 'pass')
        self.mission = Mission.objects.create(
            name='CPC-CO[20]-Medal_of_honor',
            user=self.owner,
            authors='Auteur',
            max_players=20,
            type='CO',
            version='1',
            map='mcn_hazarkot',
        )
        self.other_mission = Mission.objects.create(
            name='CPC-CO[10]-Unrelated',
            user=self.owner,
            authors='Auteur',
            max_players=10,
            type='CO',
            version='1',
            map='altis',
        )
        self.session = GameSession.objects.create(
            mission=None,
            name='CPC-CO[20]-Medal_of_honor',
            map='MCN_HazarKot',
            version='1',
            start_time=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            verdict=GameSession.VERDICT_SUCCES,
        )
        self.client = Client()

    def test_upload_path_traversal_sanitized(self):
        uploaded = MagicMock()
        uploaded.name = '../../../evil_critical.pbo'
        uploaded.chunks = MagicMock(return_value=[b'PBOFAKE'])
        path, _name, filename = save_uploaded_pbo_to_temp(uploaded, self.regular)
        try:
            self.assertEqual(filename, 'evil_critical.pbo')
            temp_dir = os.path.realpath(get_upload_temp_dir(self.regular))
            self.assertEqual(
                os.path.commonpath([temp_dir, os.path.realpath(path)]),
                temp_dir,
            )
            self.assertTrue(os.path.isfile(path))
            self.assertFalse(is_safe_upload_temp_path(path, self.owner))
            self.assertTrue(is_safe_upload_temp_path(path, self.regular))
        finally:
            if os.path.isfile(path):
                os.remove(path)

    def test_verdict_blocked_without_auth(self):
        before = self.session.verdict
        self.client.post(
            f'/sessions/{self.session.id}/',
            {'set_verdict': '1', 'verdict': GameSession.VERDICT_EFFACER},
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.verdict, before)

    def test_associate_blocked_for_non_superuser(self):
        self.client.login(username='regular', password='pass')
        self.client.post(
            f'/sessions/{self.session.id}/',
            {'mission_id': self.other_mission.id},
        )
        self.session.refresh_from_db()
        self.assertIsNone(self.session.mission_id)

    def test_legacy_create_user_blocked_non_staff(self):
        self.client.login(username='regular', password='pass')
        resp = self.client.post(
            '/legacy/create_user_from_linkeduser/',
            {'linkedUser': 'elevated_mm_user'},
        )
        self.assertIn(resp.status_code, (302, 403))
        self.assertFalse(User.objects.filter(username='elevated_mm_user').exists())

    def test_media_route_gated_by_setting(self):
        import importlib
        import gdc_kraken.urls as urls_mod

        with self.settings(SERVE_MEDIA=False, DEBUG=False):
            importlib.reload(urls_mod)
            clear_url_caches()
            self.assertFalse(
                any('media' in str(getattr(p, 'pattern', '')) for p in urls_mod.urlpatterns)
            )

        with self.settings(SERVE_MEDIA=True, DEBUG=False):
            importlib.reload(urls_mod)
            clear_url_caches()
            self.assertTrue(
                any('media' in str(getattr(p, 'pattern', '')) for p in urls_mod.urlpatterns)
            )

        importlib.reload(urls_mod)
        clear_url_caches()
