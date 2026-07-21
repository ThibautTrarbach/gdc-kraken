import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from gdc_storm.ocap_maps import (
    build_ocap_leaflet_config,
    fetch_ocap_worlds,
    get_mission_map_config,
    is_local_map_ready,
    map_code_candidates,
    meta_max_zoom,
    resolve_safe_tile_path,
    sync_ocap_world,
)


SAMPLE_WORLDS = {
    'stratis': {
        'worldName': 'stratis',
        'displayName': 'Stratis',
        'worldSize': 8192,
        'imageSize': 8192,
        'multiplier': 1,
        'maxZoom': 5,
        'attribution': 'Bohemia Interactive',
    },
    'altis': {
        'worldName': 'altis',
        'displayName': 'Altis',
        'worldSize': 30720,
        'imageSize': 30720,
        'multiplier': 1,
        'maxZoom': 7,
        'attribution': 'Bohemia Interactive',
    },
    'enoch': {
        'worldName': 'enoch',
        'displayName': 'Livonia',
        'worldSize': 12800,
        'imageSize': 12800,
        'multiplier': 1,
        'maxZoom': 6,
        'attribution': 'Bohemia Interactive',
    },
}


class OcapMapsConfigTest(TestCase):
    def test_map_code_candidates_aliases(self):
        cands = map_code_candidates('livonia')
        self.assertIn('livonia', cands)
        self.assertIn('enoch', cands)

    @patch('gdc_storm.ocap_maps.fetch_ocap_worlds', return_value=SAMPLE_WORLDS)
    def test_get_mission_map_config_prefers_ocap(self, _mock_worlds):
        cfg = get_mission_map_config('altis')
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg['provider'], 'ocap')
        self.assertEqual(cfg['world_name'], 'altis')
        self.assertIn('{z}', cfg['tile_url_remote'])
        self.assertIn('/maps/ocap/altis/topo/', cfg['tile_url_local'])
        self.assertIn('leaflet_js_url', cfg)
        self.assertIn('sync_url', cfg)

    @patch('gdc_storm.ocap_maps.fetch_ocap_worlds', return_value=SAMPLE_WORLDS)
    def test_maps_cdn_keeps_same_origin_tiles(self, _mock_worlds):
        with override_settings(OCAP_MAPS_CDN_URL='https://maps-cdn.example'):
            cfg = get_mission_map_config('altis')
        self.assertEqual(cfg['provider'], 'ocap')
        # Navigateur : always same-origin (évite Private Network Access).
        self.assertIn('/maps/ocap/altis/topo/', cfg['tile_url_local'])
        self.assertNotIn('maps-cdn.example', cfg['tile_url_local'])
        self.assertEqual(cfg['maps_cdn_url'], 'https://maps-cdn.example')
        self.assertIn('maps.ocap2.com', cfg['tile_url_remote'])
        self.assertIn('/maps/ocap/sync/', cfg['sync_url'])
        self.assertNotIn('maps-cdn.example', cfg['sync_url'])
        self.assertTrue(cfg.get('maps_cdn_proxied'))

    @patch('gdc_storm.ocap_maps.fetch_ocap_worlds', return_value=SAMPLE_WORLDS)
    def test_livonia_alias_resolves_enoch(self, _mock_worlds):
        cfg = get_mission_map_config('livonia')
        self.assertEqual(cfg['provider'], 'ocap')
        self.assertEqual(cfg['world_name'], 'enoch')

    @patch('gdc_storm.ocap_maps.fetch_ocap_worlds', return_value={})
    def test_fallback_arma3map_when_ocap_empty(self, _mock_worlds):
        cfg = get_mission_map_config('altis')
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg['provider'], 'arma3map')
        self.assertEqual(cfg['map_key'], 'altis')

    @patch('gdc_storm.ocap_maps.fetch_ocap_worlds', return_value={})
    def test_unknown_map_returns_none(self, _mock_worlds):
        self.assertIsNone(get_mission_map_config('unknown_map_xyz_zzz'))

    def test_build_ocap_leaflet_config_fields(self):
        cfg = build_ocap_leaflet_config('stratis', SAMPLE_WORLDS['stratis'])
        self.assertEqual(cfg['max_native_zoom'], 5)
        self.assertEqual(cfg['max_zoom'], 7)
        self.assertEqual(cfg['multiplier'], 1.0)

    def test_meta_max_zoom_computed_from_image_size(self):
        self.assertEqual(meta_max_zoom({}, 30720), 7)
        self.assertEqual(meta_max_zoom({'maxZoom': 4}, 30720), 4)

    @patch('gdc_storm.ocap_maps.requests.get')
    def test_fetch_worlds_falls_back_to_bundled(self, mock_get):
        mock_get.side_effect = Exception('403 Forbidden')
        worlds = fetch_ocap_worlds(force=True)
        self.assertIn('altis', worlds)
        self.assertGreater(len(worlds), 50)
        cfg = get_mission_map_config('altis')
        self.assertEqual(cfg['provider'], 'ocap')
        self.assertGreater(cfg['max_native_zoom'], 0)


class OcapLocalTilesTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.override = override_settings(OCAP_MAPS_STORAGE_PATH=self.tmp)
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_is_local_map_ready_and_safe_path(self):
        world_dir = Path(self.tmp) / 'stratis' / 'topo' / '0' / '0'
        world_dir.mkdir(parents=True)
        tile = world_dir / '0.png'
        tile.write_bytes(b'\x89PNG\r\n\x1a\n')
        self.assertTrue(is_local_map_ready('stratis'))
        resolved = resolve_safe_tile_path('stratis', 'topo/0/0/0.png')
        self.assertEqual(resolved, tile.resolve())
        self.assertIsNone(resolve_safe_tile_path('stratis', '../secret.txt'))
        self.assertIsNone(resolve_safe_tile_path('../x', 'topo/0/0/0.png'))

    def test_resolve_tile_fallback_without_topo(self):
        world_dir = Path(self.tmp) / 'brf_sumava' / '1' / '0'
        world_dir.mkdir(parents=True)
        tile = world_dir / '0.png'
        tile.write_bytes(b'\x89PNG\r\n\x1a\n')
        found = resolve_safe_tile_path('brf_sumava', 'topo/1/0/0.png')
        self.assertEqual(found, tile.resolve())

    @patch('gdc_storm.ocap_maps.fetch_ocap_archives')
    def test_sync_dry_run(self, mock_archives):
        mock_archives.return_value = {
            'stratis': {
                'worldName': 'stratis',
                'url': 'https://archives.ocap2.com/stratis.7z',
                'etag': 'abc',
                'size': 123,
            }
        }
        result = sync_ocap_world('stratis', dry_run=True)
        self.assertEqual(result['status'], 'would-sync')
        self.assertFalse(is_local_map_ready('stratis'))

    @patch('gdc_storm.ocap_maps.fetch_ocap_archives')
    @patch('gdc_storm.ocap_maps.resolve_ocap_world_meta')
    def test_command_dry_run_world(self, mock_resolve, mock_archives):
        mock_archives.return_value = {
            'altis': {
                'worldName': 'altis',
                'url': 'https://archives.ocap2.com/altis.7z',
                'etag': 'e1',
                'size': 10,
            }
        }
        mock_resolve.return_value = ('altis', SAMPLE_WORLDS['altis'])
        call_command('sync_ocap_map', '--world', 'altis', '--dry-run')


class OcapMapSyncApiTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.override = override_settings(OCAP_MAPS_STORAGE_PATH=self.tmp)
        self.override.enable()
        self.user = User.objects.create_user(username='ocapuser', password='pass')
        self.client = Client()
        self.client.force_login(self.user)

    def tearDown(self):
        self.override.disable()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sync_status_missing(self):
        url = reverse('ocap_map_sync')
        resp = self.client.get(url, {'world': 'stratis'})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'missing')
        self.assertFalse(data['local_ready'])

    def test_sync_status_missing_anonymous(self):
        anon = Client()
        url = reverse('ocap_map_sync')
        resp = anon.get(url, {'world': 'stratis'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'missing')

    @patch('gdc_storm.views.start_sync_ocap_world_async')
    def test_sync_post_queues(self, mock_start):
        mock_start.return_value = {'status': 'queued', 'local_ready': False}
        url = reverse('ocap_map_sync')
        resp = self.client.post(
            url,
            data=json.dumps({'world': 'stratis'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'queued')
        mock_start.assert_called_once()

    def test_serve_tile(self):
        world_dir = Path(self.tmp) / 'stratis' / 'topo' / '0' / '0'
        world_dir.mkdir(parents=True)
        tile = world_dir / '0.png'
        tile.write_bytes(b'\x89PNG\r\n\x1a\nfake')
        url = reverse('serve_ocap_map_tile', kwargs={'world': 'stratis', 'rest': 'topo/0/0/0.png'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'image/png')
