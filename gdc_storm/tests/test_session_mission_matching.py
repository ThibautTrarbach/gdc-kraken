import json
from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase
from io import StringIO

from gdc_storm.models import ApiToken, GameSession, Mission
from gdc_storm.utils import find_mission_for_session, find_missions_for_session


class FindMissionForSessionTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pass')
        self.mission = Mission.objects.create(
            name='CPC-CO[20]-Medal_of_honor',
            user=self.user,
            authors='Auteur',
            max_players=20,
            type='CO',
            version='1',
            map='mcn_hazarkot',
        )

    def test_matches_map_case_insensitive(self):
        mission, name, version, map_norm = find_mission_for_session(
            'CPC-CO[20]-Medal_of_honor-V2',
            'MCN_HazarKot',
        )
        self.assertEqual(mission, self.mission)
        self.assertEqual(name, 'CPC-CO[20]-Medal_of_honor')
        self.assertEqual(version, '2')
        self.assertEqual(map_norm, 'mcn_hazarkot')

    def test_matches_name_case_insensitive(self):
        mission, *_ = find_mission_for_session(
            'cpc-co[20]-medal_of_honor',
            'enoch',
        )
        # wrong map → no match
        self.assertIsNone(mission)

        mission, name, version, map_norm = find_mission_for_session(
            'cpc-co[20]-medal_of_honor',
            'MCN_HAZARKOT',
        )
        self.assertEqual(mission, self.mission)
        self.assertEqual(name, 'cpc-co[20]-medal_of_honor')
        self.assertEqual(version, '')
        self.assertEqual(map_norm, 'mcn_hazarkot')

    def test_no_match_different_slots(self):
        Mission.objects.create(
            name='CPC-CO[17]-Trompette_du_dessert',
            user=self.user,
            authors='Auteur',
            max_players=17,
            type='CO',
            version='1',
            map='enoch',
        )
        matches, *_ = find_missions_for_session(
            'CPC-CO[23]-Trompette_du_dessert',
            'Enoch',
        )
        self.assertEqual(matches, [])


class ApiCreateGamesessionMatchingTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pass')
        self.token = ApiToken.objects.create(key='test-token', name='test', is_active=True)
        self.mission = Mission.objects.create(
            name='CPC-CO[20]-Livonia_le_classico',
            user=self.user,
            authors='Auteur',
            max_players=20,
            type='CO',
            version='1',
            map='enoch',
        )
        self.client = Client()

    def test_api_links_mission_despite_map_case(self):
        start = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc).timestamp()
        response = self.client.post(
            '/api/gamesessions/',
            data=json.dumps({
                'mission_name': 'CPC-CO[20]-Livonia_le_classico-V3',
                'map': 'Enoch',
                'start_time': start,
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION='test-token',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['success'])
        self.assertTrue(payload['mission_found'])
        session = GameSession.objects.get(id=payload['session_id'])
        self.assertEqual(session.mission_id, self.mission.id)
        self.assertEqual(session.map, 'enoch')
        self.assertEqual(session.name, 'CPC-CO[20]-Livonia_le_classico')
        self.assertEqual(session.version, '3')


class SessionDetailAssociateButtonTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='admin', password='pass', email='a@b.c'
        )
        self.mission = Mission.objects.create(
            name='CPC-CO[20]-Medal_of_honor',
            user=self.user,
            authors='Auteur',
            max_players=20,
            type='CO',
            version='1',
            map='mcn_hazarkot',
        )
        self.session = GameSession.objects.create(
            mission=None,
            name='CPC-CO[20]-Medal_of_honor',
            map='MCN_HazarKot',
            version='1',
            start_time=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        )
        self.client = Client()
        self.client.login(username='admin', password='pass')

    def test_lier_lists_candidates_despite_map_case(self):
        response = self.client.get(f'/sessions/{self.session.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'associate-mission-btn')
        self.assertContains(response, self.mission.name)
        self.assertNotContains(
            response,
            "Aucune mission correspondante n'a été trouvée",
        )

    def test_lier_post_links_and_normalizes_map(self):
        response = self.client.post(
            f'/sessions/{self.session.id}/',
            {'mission_id': self.mission.id},
        )
        self.assertEqual(response.status_code, 302)
        self.session.refresh_from_db()
        self.assertEqual(self.session.mission_id, self.mission.id)
        self.assertEqual(self.session.map, 'mcn_hazarkot')


class RelinkOrphanSessionsCommandTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pass')
        self.mission = Mission.objects.create(
            name='CPC-CO[20]-Metal-Milicia',
            user=self.user,
            authors='Auteur',
            max_players=20,
            type='CO',
            version='1',
            map='mcn_aliabad',
        )
        self.orphan = GameSession.objects.create(
            mission=None,
            name='CPC-CO[20]-Metal-Milicia',
            map='MCN_Aliabad',
            version='1',
            start_time=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        )

    def test_dry_run_does_not_link(self):
        out = StringIO()
        call_command('relink_orphan_sessions', stdout=out)
        self.orphan.refresh_from_db()
        self.assertIsNone(self.orphan.mission_id)
        self.assertEqual(self.orphan.map, 'MCN_Aliabad')
        self.assertIn('link:', out.getvalue())
        self.assertIn('DRY-RUN', out.getvalue())

    def test_apply_links_and_normalizes_map(self):
        out = StringIO()
        call_command('relink_orphan_sessions', '--apply', stdout=out)
        self.orphan.refresh_from_db()
        self.assertEqual(self.orphan.mission_id, self.mission.id)
        self.assertEqual(self.orphan.map, 'mcn_aliabad')
        self.assertIn('APPLY', out.getvalue())

    def test_apply_skips_ambiguous(self):
        Mission.objects.create(
            name='CPC-CO[20]-Metal-Milicia',
            user=self.user,
            authors='Autre',
            max_players=20,
            type='CO',
            version='2',
            map='MCN_Aliabad',  # iexact same map, duplicate name
        )
        # Two missions match casefold name + iexact map
        out = StringIO()
        call_command('relink_orphan_sessions', '--apply', stdout=out)
        self.orphan.refresh_from_db()
        self.assertIsNone(self.orphan.mission_id)
        self.assertIn('ambiguës', out.getvalue())
