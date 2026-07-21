"""Tests de non-régression des correctifs de sécurité critiques."""
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import clear_url_caches, reverse

from gdc_storm.models import GameSession, GameSessionPlayer, Mission, Player
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

    def test_player_mapping_blocks_unrelated_unlinked_player(self):
        victim_player = Player.objects.create(name='RealParticipant')
        unrelated = User.objects.create_user('attacker', 'a@evil.com', 'pass')
        session = GameSession.objects.create(
            mission=None,
            name='CPC-CO[20]-Medal_of_honor',
            map='altis',
            version='1',
            start_time=datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc),
            verdict=GameSession.VERDICT_INCONNU,
        )
        GameSessionPlayer.objects.create(session=session, player=victim_player, role='SL')
        self.client.login(username='attacker', password='pass')
        self.client.post(
            reverse('player_mapping'),
            {'players': [str(victim_player.id)]},
        )
        self.assertFalse(unrelated.players.filter(id=victim_player.id).exists())
        self.client.post(
            reverse('session_detail', args=[session.id]),
            {'set_verdict': '1', 'verdict': GameSession.VERDICT_SUCCES},
        )
        session.refresh_from_db()
        self.assertEqual(session.verdict, GameSession.VERDICT_INCONNU)

    def test_player_mapping_allows_name_match(self):
        matched = Player.objects.create(name='regular')
        self.client.login(username='regular', password='pass')
        self.client.post(
            reverse('player_mapping'),
            {'players': [str(matched.id)]},
        )
        self.assertTrue(self.regular.players.filter(id=matched.id).exists())

    def test_participant_cannot_set_effacer_verdict(self):
        player = Player.objects.create(name='regular')
        player.users.add(self.regular)
        session = GameSession.objects.create(
            mission=None,
            name='CPC-CO[20]-Medal_of_honor',
            map='altis',
            version='1',
            start_time=datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc),
            verdict=GameSession.VERDICT_INCONNU,
        )
        GameSessionPlayer.objects.create(session=session, player=player, role='SL')
        self.client.login(username='regular', password='pass')
        self.client.post(
            reverse('session_detail', args=[session.id]),
            {'set_verdict': '1', 'verdict': GameSession.VERDICT_EFFACER},
        )
        session.refresh_from_db()
        self.assertEqual(session.verdict, GameSession.VERDICT_INCONNU)

    @override_settings(
        CACHES={
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            }
        }
    )
    def test_login_rate_limit_returns_429(self):
        cache.clear()
        for _ in range(21):
            resp = self.client.post(
                reverse('login'),
                {'username': 'nobody', 'password': 'wrong'},
            )
        self.assertEqual(resp.status_code, 429)
