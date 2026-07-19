"""Tests de non-régression des bugs fonctionnels."""
import json
import os
import tempfile
import time
from datetime import datetime, timezone

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone as dj_tz

from gdc_storm.models import ApiToken, GameSession, GameSessionPlayer, Mission, Player
from gdc_storm.views import clean_temp_files, mission_pbo_candidate_names


class FunctionalBugsRegressionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('player1', 'p@p.com', 'pass')
        Group.objects.get_or_create(name='Mission Maker')
        mm = Group.objects.get(name='Mission Maker')
        self.owner = User.objects.create_user('owner', 'o@o.com', 'pass')
        self.owner.groups.add(mm)
        self.mission = Mission.objects.create(
            name='CPC-CO[10]-Alpha',
            user=self.owner,
            authors='A',
            min_players=5,
            max_players=20,
            type='CO',
            version='1',
            map='altis',
        )
        self.mission2 = Mission.objects.create(
            name='CPC-CO[30]-Bravo',
            user=self.owner,
            authors='A',
            min_players=15,
            max_players=10,
            type='CO',
            version='1',
            map='altis',
        )
        self.player = Player.objects.create(name='player1')
        self.player.users.add(self.user)
        self.session = GameSession.objects.create(
            mission=self.mission,
            name='CPC-CO[10]-Alpha',
            map='altis',
            version='1',
            start_time=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            verdict=GameSession.VERDICT_SUCCES,
        )
        GameSessionPlayer.objects.create(
            session=self.session, player=self.player, role='SL', status='VIVANT'
        )
        self.client = Client()
        self.token = ApiToken.objects.create(key='test-token-func', name='func', is_active=True)

    def test_verdict_filter_works(self):
        self.client.login(username='player1', password='pass')
        resp = self.client.get(
            reverse('user_profile', args=[self.user.id]),
            {'filter_verdict': 'succ'},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.context['sessions_data']
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['session'].verdict, GameSession.VERDICT_SUCCES)

        resp_empty = self.client.get(
            reverse('user_profile', args=[self.user.id]),
            {'filter_verdict': 'zzzz'},
        )
        self.assertEqual(len(resp_empty.context['sessions_data']), 0)

    def test_sort_min_uses_min_players(self):
        resp = self.client.get(reverse('mission_list'), {'sort': 'min', 'order': 'asc'})
        self.assertEqual(resp.status_code, 200)
        missions = list(resp.context['missions'])
        sorted_by_min = sorted(missions, key=lambda m: m.min_players or 0)
        self.assertEqual([m.id for m in missions], [m.id for m in sorted_by_min])

    def test_clean_temp_uses_realtime(self):
        with tempfile.TemporaryDirectory() as td:
            old_file = os.path.join(td, 'old.tmp')
            with open(old_file, 'w') as f:
                f.write('x')
            old_ts = time.time() - 7200
            os.utime(old_file, (old_ts, old_ts))
            os.utime(td, (old_ts, old_ts))
            clean_temp_files(td, max_age_seconds=3600)
            self.assertFalse(os.path.isfile(old_file))

    def test_delete_mission_removes_pbo(self):
        orphan_mission = Mission.objects.create(
            name='CPC-CO[08]-OrphanDelete',
            user=self.owner,
            authors='A',
            min_players=4,
            max_players=8,
            type='CO',
            version='1',
            map='altis',
        )
        with tempfile.TemporaryDirectory() as storage:
            cand = mission_pbo_candidate_names(orphan_mission)[0]
            pbo_path = os.path.join(storage, cand)
            with open(pbo_path, 'wb') as f:
                f.write(b'PBO')
            self.client.login(username='owner', password='pass')
            with override_settings(MISSIONS_PBO_STORAGE_PATH=storage):
                self.client.post(reverse('delete_mission', args=[orphan_mission.id]))
            self.assertFalse(Mission.objects.filter(id=orphan_mission.id).exists())
            self.assertFalse(os.path.isfile(pbo_path))

    def test_api_aware_datetime_and_bad_json_400(self):
        start = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc).timestamp()
        resp = self.client.post(
            reverse('api_create_gamesession'),
            data=json.dumps({
                'mission_name': 'CPC-CO[10]-Alpha',
                'map': 'altis',
                'start_time': start,
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION='test-token-func',
        )
        self.assertEqual(resp.status_code, 200)
        sess = GameSession.objects.get(id=resp.json()['session_id'])
        self.assertTrue(dj_tz.is_aware(sess.start_time))

        bad = self.client.post(
            reverse('api_create_gamesession'),
            data='{not json',
            content_type='application/json',
            HTTP_AUTHORIZATION='test-token-func',
        )
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(bad.json().get('success', True))
