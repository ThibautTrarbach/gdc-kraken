"""Mesure / garde-fous sur le nombre de requêtes SQL (anti N+1)."""
from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from gdc_storm.models import GameSession, GameSessionPlayer, MapName, Mission, Player


@override_settings(DEBUG=True)  # nécessaire pour CaptureQueriesContext
class QueryCountRegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('optuser', 'o@o.com', 'pass')
        cls.map_altis = MapName.objects.create(code_name='altis', display_name='Altis')
        cls.map_stratis = MapName.objects.create(code_name='stratis', display_name='Stratis')
        cls.missions = []
        for i in range(10):
            m = Mission.objects.create(
                name=f'CPC-CO[{10+i:02d}]-OptMission{i}',
                user=cls.user,
                authors='A',
                min_players=5,
                max_players=20,
                type='CO',
                version='1',
                map='altis' if i % 2 == 0 else 'stratis',
            )
            cls.missions.append(m)
        cls.players = []
        for i in range(15):
            p = Player.objects.create(name=f'OptPlayer{i}')
            cls.players.append(p)
        cls.players[0].users.add(cls.user)
        cls.sessions = []
        for i in range(20):
            s = GameSession.objects.create(
                mission=cls.missions[i % 10],
                name=cls.missions[i % 10].name,
                map=cls.missions[i % 10].map,
                version='1',
                start_time=datetime(2026, 1, 1, 12, i % 60, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 1, 13, i % 60, tzinfo=timezone.utc),
                verdict=GameSession.VERDICT_SUCCES if i % 2 else GameSession.VERDICT_INCONNU,
            )
            cls.sessions.append(s)
            for j in range(5):
                GameSessionPlayer.objects.create(
                    session=s,
                    player=cls.players[j],
                    role='Rifleman',
                    status='VIVANT' if j % 2 == 0 else 'MORT',
                )
        for i in range(8):
            s = GameSession.objects.create(
                mission=None,
                name=f'CPC-CO[20]-Orphan{i}',
                map='altis',
                version='1',
                start_time=datetime(2026, 2, 1, 12, i, tzinfo=timezone.utc),
            )
            GameSessionPlayer.objects.create(
                session=s, player=cls.players[0], role='SL', status='VIVANT'
            )

    def setUp(self):
        self.client = Client()
        self.client.login(username='optuser', password='pass')

    def _count(self, path):
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(path)
        self.assertEqual(resp.status_code, 200)
        return len(ctx.captured_queries)

    def test_mission_detail_query_budget(self):
        self.assertLessEqual(
            self._count(reverse('mission_detail', args=[self.missions[0].id])),
            10,
        )

    def test_orphan_sessions_query_budget(self):
        self.assertLessEqual(self._count(reverse('orphan_sessions')), 8)

    def test_user_profile_query_budget(self):
        self.assertLessEqual(
            self._count(reverse('user_profile', args=[self.user.id])),
            30,
        )

    def test_map_list_query_budget(self):
        self.assertLessEqual(self._count(reverse('map_list')), 6)

    def test_player_mapping_post_query_budget(self):
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.post(
                reverse('player_mapping'),
                {'players': [str(self.players[0].id)]},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertLessEqual(len(ctx.captured_queries), 20)

    def test_find_missions_filters_name_in_sql(self):
        from gdc_storm.utils import find_missions_for_session
        with CaptureQueriesContext(connection) as ctx:
            matches, *_ = find_missions_for_session(
                self.missions[0].name, 'altis'
            )
        self.assertGreaterEqual(len(matches), 1)
        self.assertEqual(len(ctx.captured_queries), 1)
        self.assertIn('name', ctx.captured_queries[0]['sql'].lower())
