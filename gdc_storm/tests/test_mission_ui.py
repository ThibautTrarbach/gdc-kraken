from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from gdc_storm.models import GameSession, Mission
from gdc_storm.templatetags.dict_extras import mission_display_name, mission_short_name


class MissionDisplayNameFilterTest(TestCase):
    def test_strips_prefix_and_replaces_separators(self):
        self.assertEqual(
            mission_display_name('CPC-CO[20]-Ma_Belle-Mission'),
            'Ma Belle Mission',
        )

    def test_without_prefix(self):
        self.assertEqual(
            mission_display_name('Foo_Bar-Baz'),
            'Foo Bar Baz',
        )

    def test_short_name_alias(self):
        self.assertEqual(
            mission_short_name('CPC-TVT[12]-Alpha_Raid'),
            'Alpha Raid',
        )

    def test_empty(self):
        self.assertEqual(mission_display_name(''), '')
        self.assertEqual(mission_display_name(None), '')


class MissionOwnerAndWinRateTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser('admin', 'a@a.com', 'pass')
        self.owner = User.objects.create_user('owner', 'o@o.com', 'pass')
        self.other = User.objects.create_user('other', 'x@x.com', 'pass')
        self.mission = Mission.objects.create(
            name='CPC-CO[20]-Win_Rate-Test',
            user=self.owner,
            authors='A',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
        )
        now = timezone.now()
        for verdict in (
            GameSession.VERDICT_SUCCES,
            GameSession.VERDICT_SUCCES,
            GameSession.VERDICT_ECHEC,
            GameSession.VERDICT_INCONNU,
            GameSession.VERDICT_PVP,
        ):
            GameSession.objects.create(
                mission=self.mission,
                name=self.mission.name,
                map='altis',
                version='1',
                start_time=now,
                verdict=verdict,
            )

    def test_superuser_can_change_owner(self):
        self.client.login(username='admin', password='pass')
        resp = self.client.post(
            reverse('mission_detail', args=[self.mission.id]),
            {'update_owner': '1', 'user': self.other.id},
        )
        self.assertEqual(resp.status_code, 302)
        self.mission.refresh_from_db()
        self.assertEqual(self.mission.user_id, self.other.id)

    def test_non_superuser_cannot_change_owner(self):
        self.client.login(username='owner', password='pass')
        resp = self.client.post(
            reverse('mission_detail', args=[self.mission.id]),
            {'update_owner': '1', 'user': self.other.id},
        )
        self.mission.refresh_from_db()
        self.assertEqual(self.mission.user_id, self.owner.id)
        self.assertNotContains(resp, 'id="open-owner-popup"')
        self.assertNotContains(resp, 'id="owner-popup"')

    def test_mission_detail_win_rate(self):
        self.client.login(username='admin', password='pass')
        resp = self.client.get(reverse('mission_detail', args=[self.mission.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '67 % (2/3)')
        self.assertContains(resp, 'Win Rate Test')

    def test_mission_list_win_rate(self):
        resp = self.client.get(reverse('mission_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '67 % (2/3)')
        self.assertContains(resp, 'Win Rate Test')
        self.assertContains(resp, 'Ratio victoire')
