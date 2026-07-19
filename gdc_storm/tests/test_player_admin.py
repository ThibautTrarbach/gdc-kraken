"""Tests pour la vue admin fusion / liaison de joueurs."""
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from gdc_storm.models import GameSession, GameSessionPlayer, Player


class PlayerAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@a.com', 'pass')
        self.regular = User.objects.create_user('regular', 'r@r.com', 'pass')
        self.client = Client()
        self.url = reverse('player_admin')

    def test_requires_login(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)

    def test_requires_superuser(self):
        self.client.login(username='regular', password='pass')
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_access(self):
        self.client.login(username='admin', password='pass')
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Admin joueurs')

    def test_link_user(self):
        p1 = Player.objects.create(name='PseudoA')
        p2 = Player.objects.create(name='PseudoB')
        self.client.login(username='admin', password='pass')
        resp = self.client.post(self.url, {
            'action': 'link_user',
            'player_ids': [str(p1.id), str(p2.id)],
            'user_id': str(self.regular.id),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn(self.regular, p1.users.all())
        self.assertIn(self.regular, p2.users.all())

    def test_unlink_user(self):
        p1 = Player.objects.create(name='PseudoC')
        p1.users.add(self.regular)
        self.client.login(username='admin', password='pass')
        resp = self.client.post(self.url, {
            'action': 'unlink_user',
            'player_id': str(p1.id),
            'user_id': str(self.regular.id),
            'next': self.url,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn(self.regular, p1.users.all())

    def test_merge_players(self):
        keep = Player.objects.create(name='KeepMe')
        drop = Player.objects.create(name='DropMe')
        other = User.objects.create_user('other', 'o@o.com', 'pass')
        drop.users.add(other)
        session = GameSession.objects.create(
            name='TestMission',
            map='altis',
            version='1',
            start_time='2024-01-01T12:00:00Z',
        )
        GameSessionPlayer.objects.create(
            session=session, player=drop, role='Rifleman', status='VIVANT'
        )
        self.client.login(username='admin', password='pass')
        resp = self.client.post(self.url, {
            'action': 'merge',
            'player_ids': [str(keep.id), str(drop.id)],
            'target_player_id': str(keep.id),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Player.objects.filter(id=drop.id).exists())
        self.assertEqual(GameSessionPlayer.objects.filter(player=keep).count(), 1)
        self.assertIn(other, keep.users.all())

    def test_merge_conflict_drops_duplicate_session(self):
        keep = Player.objects.create(name='Keep2')
        drop = Player.objects.create(name='Drop2')
        session = GameSession.objects.create(
            name='SameSession',
            map='altis',
            version='1',
            start_time='2024-02-01T12:00:00Z',
        )
        GameSessionPlayer.objects.create(
            session=session, player=keep, role='Medic', status='VIVANT'
        )
        GameSessionPlayer.objects.create(
            session=session, player=drop, role='Medic', status='MORT'
        )
        self.client.login(username='admin', password='pass')
        resp = self.client.post(self.url, {
            'action': 'merge',
            'player_ids': [str(keep.id), str(drop.id)],
            'target_player_id': str(keep.id),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Player.objects.filter(id=drop.id).exists())
        self.assertEqual(GameSessionPlayer.objects.filter(player=keep).count(), 1)
        self.assertEqual(
            GameSessionPlayer.objects.get(player=keep).status, 'VIVANT'
        )
