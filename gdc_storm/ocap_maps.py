"""Catalogue et config Leaflet pour les fonds de carte OCAP2."""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from pathlib import Path

import requests
from django.conf import settings
from django.core.cache import cache

from gdc_storm.arma3map import (
    MAP_CODE_TO_ARMA3MAP,
    _MAP_ALIASES,
    get_arma3map_config,
    with_local_map_assets,
)

logger = logging.getLogger(__name__)

_PROGRESS_EVERY_BYTES = 50 * 1024 * 1024


def _human_size(num: int | float | None) -> str:
    if num is None:
        return '?'
    size = float(num)
    if size < 0:
        return '?'
    for unit in ('o', 'Ko', 'Mo', 'Go'):
        if size < 1024 or unit == 'Go':
            if unit == 'o':
                return f'{int(size)} o'
            return f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} Go'


def _log_download_progress(
    world: str,
    downloaded: int,
    total: int | None,
    state: dict[str, int],
) -> None:
    if total and total > 0:
        pct = min(100, int(downloaded * 100 / total))
        last_pct = state.get('last_pct', -1)
        if pct >= last_pct + 10 or downloaded >= total:
            state['last_pct'] = pct
            logger.info(
                '[%s] Téléchargement %s%% (%s / %s)',
                world,
                pct,
                _human_size(downloaded),
                _human_size(total),
            )
        return
    last_bytes = state.get('last_bytes', 0)
    if downloaded - last_bytes >= _PROGRESS_EVERY_BYTES:
        state['last_bytes'] = downloaded
        logger.info('[%s] Téléchargement %s…', world, _human_size(downloaded))


OCAP_WORLDS_CACHE_KEY = 'gdc_storm_ocap_worlds_v1'
OCAP_ARCHIVES_CACHE_KEY = 'gdc_storm_ocap_archives_v1'
OCAP_WORLDS_CACHE_TTL = 60 * 60 * 24
OCAP_ARCHIVES_CACHE_TTL = 60 * 60 * 6
_BUNDLED_WORLDS_PATH = Path(__file__).resolve().parent / 'data' / 'ocap_worlds.json'

# CDN OCAP bloque souvent les UA Python / IP datacenter (403).
_OCAP_HTTP_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json,text/plain,*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://maps.ocap2.com/',
}

# Alias GDC / PBO -> worldName OCAP (minuscules).
_OCAP_ALIASES: dict[str, str] = {
    **{k.lower(): str(v).lower() for k, v in _MAP_ALIASES.items()},
    'chernarus_a3': 'chernarus',
    'cup_chernarus': 'chernarus',
    'cup_chernarus_a3': 'cup_chernarus_a3',
    'livonia': 'enoch',
}

_sync_locks: dict[str, threading.Lock] = {}
_sync_locks_guard = threading.Lock()


def ocap_worlds_url() -> str:
    return getattr(settings, 'OCAP_WORLDS_URL', 'https://maps.ocap2.com/worlds.json')


def ocap_archives_list_url() -> str:
    return getattr(settings, 'OCAP_ARCHIVES_LIST_URL', 'https://archives.ocap2.com/list')


def ocap_tiles_base_url() -> str:
    return getattr(settings, 'OCAP_TILES_BASE_URL', 'https://maps.ocap2.com').rstrip('/')


def ocap_maps_cdn_url() -> str:
    return getattr(settings, 'OCAP_MAPS_CDN_URL', '').rstrip('/')


def ocap_maps_cdn_secret() -> str:
    return getattr(settings, 'OCAP_MAPS_CDN_SECRET', '') or ''


def ocap_maps_storage_path() -> Path:
    raw = getattr(settings, 'OCAP_MAPS_STORAGE_PATH', None)
    if raw:
        return Path(raw)
    return Path(settings.MEDIA_ROOT) / 'ocap_maps'


def map_code_candidates(map_code: str) -> list[str]:
    """Candidats worldName à tester (ordre de priorité)."""
    code = (map_code or '').strip().lower()
    if not code:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add(value: str | None) -> None:
        if not value:
            return
        v = value.strip().lower()
        if not v or v in seen:
            return
        seen.add(v)
        out.append(v)

    add(code)
    add(_OCAP_ALIASES.get(code))
    arma_key = MAP_CODE_TO_ARMA3MAP.get(code)
    if arma_key:
        add(str(arma_key).lower())
        add(_OCAP_ALIASES.get(str(arma_key).lower()))
    return out


def _index_worlds_payload(payload) -> dict[str, dict]:
    index: dict[str, dict] = {}
    items = payload if isinstance(payload, list) else list(payload.values())
    for item in items:
        if not isinstance(item, dict):
            continue
        name = (item.get('worldName') or item.get('worldname') or item.get('name') or '')
        name = str(name).strip()
        if not name:
            continue
        index[name.lower()] = item
    return index


def _load_bundled_ocap_worlds() -> dict[str, dict]:
    path = _BUNDLED_WORLDS_PATH
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        logger.warning('Catalogue OCAP embarqué illisible (%s): %s', path, exc)
        return {}
    return _index_worlds_payload(payload)


def meta_max_zoom(meta: dict, image_size: float = 0) -> int:
    """maxZoom OCAP ; calcule depuis imageSize (tuiles 256px) si absent."""
    raw = meta.get('maxZoom')
    if raw is not None and str(raw).strip() != '':
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    size = float(image_size or meta.get('imageSize') or meta.get('worldSize') or 0)
    if size >= 256:
        return int(math.ceil(math.log2(size / 256.0)))
    return 0


def fetch_ocap_worlds(*, force: bool = False) -> dict[str, dict]:
    """Retourne worldName.lower() -> meta map.json (cache Django)."""
    if not force:
        cached = cache.get(OCAP_WORLDS_CACHE_KEY)
        if isinstance(cached, dict) and cached:
            return cached
    url = ocap_worlds_url()
    try:
        resp = requests.get(url, timeout=30, headers=_OCAP_HTTP_HEADERS)
        resp.raise_for_status()
        index = _index_worlds_payload(resp.json())
        if index:
            cache.set(OCAP_WORLDS_CACHE_KEY, index, OCAP_WORLDS_CACHE_TTL)
            return index
    except Exception as exc:
        logger.warning('OCAP worlds.json indisponible (%s): %s', url, exc)

    cached = cache.get(OCAP_WORLDS_CACHE_KEY)
    if isinstance(cached, dict) and cached:
        return cached

    bundled = _load_bundled_ocap_worlds()
    if bundled:
        logger.info(
            'Utilisation du catalogue OCAP embarqué (%d worlds) — CDN distant inaccessible',
            len(bundled),
        )
        cache.set(OCAP_WORLDS_CACHE_KEY, bundled, OCAP_WORLDS_CACHE_TTL)
        return bundled
    return {}


def fetch_ocap_archives(*, force: bool = False) -> dict[str, dict]:
    if not force:
        cached = cache.get(OCAP_ARCHIVES_CACHE_KEY)
        if isinstance(cached, dict):
            return cached
    url = ocap_archives_list_url()
    try:
        resp = requests.get(url, timeout=30, headers=_OCAP_HTTP_HEADERS)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        logger.warning('OCAP archives list indisponible (%s): %s', url, exc)
        cached = cache.get(OCAP_ARCHIVES_CACHE_KEY)
        return cached if isinstance(cached, dict) else {}

    index: dict[str, dict] = {}
    if isinstance(payload, dict):
        for key, item in payload.items():
            if not isinstance(item, dict):
                continue
            world = (item.get('worldName') or key or '').strip().lower()
            if world:
                index[world] = item
    elif isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            world = (item.get('worldName') or '').strip().lower()
            if world:
                index[world] = item
    cache.set(OCAP_ARCHIVES_CACHE_KEY, index, OCAP_ARCHIVES_CACHE_TTL)
    return index


def resolve_ocap_world_meta(map_code: str) -> tuple[str, dict] | None:
    worlds = fetch_ocap_worlds()
    if not worlds:
        return None
    for candidate in map_code_candidates(map_code):
        meta = worlds.get(candidate)
        if meta:
            world_name = str(meta.get('worldName') or candidate).strip()
            return world_name, meta
    return None


def world_storage_dir(world_name: str) -> Path:
    safe = (world_name or '').strip().replace('\\', '/').split('/')[-1]
    return ocap_maps_storage_path() / safe


def is_local_map_ready(world_name: str) -> bool:
    root = world_storage_dir(world_name)
    topo = root / 'topo'
    if topo.is_dir() and any(topo.rglob('*.png')):
        return True
    # Ancien layout : tuiles à la racine du world.
    if root.is_dir() and any(root.glob('*/*/*.png')):
        return True
    return False


def local_etag_path(world_name: str) -> Path:
    return world_storage_dir(world_name) / '.ocap_etag'


def syncing_flag_path(world_name: str) -> Path:
    return world_storage_dir(world_name) / '.ocap_syncing'


def sync_error_path(world_name: str) -> Path:
    return world_storage_dir(world_name) / '.ocap_sync_error'


def read_sync_error(world_name: str) -> str | None:
    path = sync_error_path(world_name)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding='utf-8').strip()
    except OSError:
        return None
    return text or None


def write_sync_error(world_name: str, message: str) -> None:
    path = sync_error_path(world_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((message or 'sync échouée').strip(), encoding='utf-8')


def clear_sync_error(world_name: str) -> None:
    path = sync_error_path(world_name)
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def is_syncing(world_name: str) -> bool:
    flag = syncing_flag_path(world_name)
    if not flag.is_file():
        return False
    try:
        age = time.time() - flag.stat().st_mtime
    except OSError:
        return False
    # Flag périmé après 2h (sync interrompue).
    if age > 7200:
        try:
            flag.unlink()
        except OSError:
            pass
        return False
    return True


def remote_tile_url_template(world_name: str) -> str:
    return f'{ocap_tiles_base_url()}/{world_name}/topo/{{z}}/{{x}}/{{y}}.png'


def local_tile_url_template(world_name: str) -> str:
    """URL navigateur (same-origin). Le maps-cdn n'est joint que côté serveur."""
    return f'/maps/ocap/{world_name}/topo/{{z}}/{{x}}/{{y}}.png'


def is_safe_ocap_tile_ref(world_name: str, rest: str) -> tuple[str, str] | None:
    """Valide world + chemin relatif ; retourne (world, rest_norm) ou None."""
    world = (world_name or '').strip()
    if not world or '/' in world or '\\' in world or world.startswith('.'):
        return None
    rest_norm = (rest or '').replace('\\', '/').lstrip('/')
    if not rest_norm or '..' in rest_norm.split('/'):
        return None
    return world, rest_norm


def fetch_maps_cdn_tile(world_name: str, rest: str) -> tuple[bytes, str] | None:
    """Télécharge une tuile depuis le maps-cdn (proxy serveur → CDN)."""
    cdn = ocap_maps_cdn_url()
    if not cdn:
        return None
    safe = is_safe_ocap_tile_ref(world_name, rest)
    if not safe:
        return None
    world, rest_norm = safe
    candidates = [rest_norm]
    if rest_norm.startswith('topo/'):
        candidates.append(rest_norm[len('topo/'):])
    elif not rest_norm.startswith('sat/'):
        candidates.append(f'topo/{rest_norm}')
    last_exc: Exception | None = None
    for candidate in candidates:
        url = f'{cdn}/{world}/{candidate}'
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200:
                content_type = resp.headers.get('Content-Type') or 'image/png'
                return resp.content, content_type
        except Exception as exc:
            last_exc = exc
            logger.warning('Proxy tuile maps-cdn échoué (%s): %s', url, exc)
    if last_exc is None:
        logger.debug('Tuile maps-cdn introuvable pour %s/%s', world, rest_norm)
    return None


def maps_cdn_sync_url() -> str | None:
    cdn = ocap_maps_cdn_url()
    if not cdn:
        return None
    return f'{cdn}/api/sync'


def maps_cdn_status_url(world_name: str) -> str | None:
    cdn = ocap_maps_cdn_url()
    if not cdn:
        return None
    return f'{cdn}/api/status?world={world_name}'


def probe_maps_cdn_ready(world_name: str) -> bool:
    url = maps_cdn_status_url(world_name)
    if not url:
        return False
    try:
        resp = requests.get(url, timeout=3)
        if resp.status_code != 200:
            return False
        data = resp.json()
        return bool(data.get('local_ready') or data.get('status') == 'ready')
    except Exception:
        return False


def build_ocap_leaflet_config(world_name: str, meta: dict) -> dict:
    world_size = float(meta.get('worldSize') or meta.get('imageSize') or 0)
    image_size = float(meta.get('imageSize') or world_size or 0)
    multiplier = float(meta.get('multiplier') or 1)
    max_native = meta_max_zoom(meta, image_size)
    attribution = (meta.get('attribution') or 'OCAP2').strip()
    display_name = (meta.get('displayName') or world_name).strip()
    cdn = ocap_maps_cdn_url()
    # Stockage local Storm (volume partagé) ou probe maps-cdn distant.
    mirror_ready = is_local_map_ready(world_name)
    if not mirror_ready and cdn:
        mirror_ready = probe_maps_cdn_ready(world_name)
    return {
        'provider': 'ocap',
        'world_name': world_name,
        'display_name': display_name,
        'world_size': world_size,
        'image_size': image_size,
        'multiplier': multiplier,
        'max_native_zoom': max_native,
        'max_zoom': max_native + 2,
        'min_zoom': 0,
        'default_zoom': max(0, min(3, max_native)),
        'attribution': attribution,
        # Priorité navigateur : CDN officiel OCAP2.
        'tile_url_remote': remote_tile_url_template(world_name),
        # Fallback : miroir maps-cdn (ou Django local).
        'tile_url_local': local_tile_url_template(world_name),
        'local_ready': mirror_ready,
        'prefer_local': False,
        'maps_cdn_url': cdn or '',
    }


def with_local_leaflet_only(config: dict | None) -> dict | None:
    if not config:
        return None
    from django.templatetags.static import static
    from django.urls import reverse

    # Sync / status : toujours same-origin (Storm proxy). Jamais d'IP privée maps-cdn
    # dans le navigateur (CORS, Private Network Access, réponses HTML).
    out = {
        **config,
        'leaflet_css_url': static('gdc_storm/vendor/leaflet/leaflet.css'),
        'leaflet_js_url': static('gdc_storm/vendor/leaflet/leaflet.js'),
        'markers_icon_base': static('gdc_storm/markers').rstrip('/'),
        'sync_url': reverse('ocap_map_sync'),
        'status_url': reverse('ocap_map_sync'),
    }
    if ocap_maps_cdn_url():
        out['maps_cdn_proxied'] = True
    return out



def get_ocap_map_config(map_code: str) -> dict | None:
    resolved = resolve_ocap_world_meta(map_code)
    if not resolved:
        return None
    world_name, meta = resolved
    return build_ocap_leaflet_config(world_name, meta)


def get_mission_map_config(map_code: str) -> dict | None:
    """OCAP d'abord, Arma3Map en secours."""
    ocap = get_ocap_map_config(map_code)
    if ocap:
        return with_local_leaflet_only(ocap)
    arma = with_local_map_assets(get_arma3map_config(map_code))
    if arma:
        return {**arma, 'provider': 'arma3map'}
    return None


def _sync_lock_for(world_name: str) -> threading.Lock:
    key = world_name.lower()
    with _sync_locks_guard:
        lock = _sync_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _sync_locks[key] = lock
        return lock


def sync_ocap_world(world_name: str, *, force: bool = False, dry_run: bool = False) -> dict:
    """
    Télécharge et extrait l'archive .7z OCAP pour un world.
    Retourne un dict status: ready|skipped|error + détails.
    """
    world = (world_name or '').strip()
    if not world or '/' in world or '\\' in world or '..' in world:
        return {'status': 'error', 'error': 'world invalide'}

    archives = fetch_ocap_archives()
    entry = archives.get(world.lower())
    if not entry:
        return {'status': 'error', 'error': f'archive absente pour {world}'}

    archive_url = entry.get('url')
    etag = str(entry.get('etag') or '')
    archive_size = entry.get('size')
    if not archive_url:
        return {'status': 'error', 'error': 'url archive manquante'}

    dest = world_storage_dir(world)
    etag_file = local_etag_path(world)
    if is_local_map_ready(world) and not force:
        if etag and etag_file.is_file() and etag_file.read_text(encoding='utf-8').strip() == etag:
            logger.info('[%s] Sync ignorée — etag inchangé', world)
            return {'status': 'ready', 'skipped': True, 'reason': 'etag inchangé'}
        if not etag:
            logger.info('[%s] Sync ignorée — pack déjà présent', world)
            return {'status': 'ready', 'skipped': True, 'reason': 'déjà présent'}

    if dry_run:
        return {
            'status': 'would-sync',
            'world': world,
            'url': archive_url,
            'size': archive_size,
            'etag': etag,
        }

    lock = _sync_lock_for(world)
    if not lock.acquire(blocking=False):
        logger.info('[%s] Sync déjà en cours (lock)', world)
        return {'status': 'queued', 'reason': 'sync déjà en cours'}

    flag = syncing_flag_path(world)
    try:
        dest.mkdir(parents=True, exist_ok=True)
        clear_sync_error(world)
        flag.write_text(str(time.time()), encoding='utf-8')
        logger.info(
            '[%s] Sync démarrée — archive %s — %s',
            world,
            _human_size(archive_size),
            archive_url,
        )
        result = _download_and_extract(
            world, archive_url, etag, dest, etag_file, expected_size=archive_size
        )
        if result.get('status') == 'error':
            write_sync_error(world, str(result.get('error') or 'sync échouée'))
        return result
    finally:
        try:
            if flag.is_file():
                flag.unlink()
        except OSError:
            pass
        lock.release()


def _download_and_extract(
    world: str,
    archive_url: str,
    etag: str,
    dest: Path,
    etag_file: Path,
    *,
    expected_size: int | None = None,
) -> dict:
    try:
        import py7zr
    except ImportError:
        return {'status': 'error', 'error': 'py7zr non installé'}

    tmp_dir = ocap_maps_storage_path() / '.tmp'
    tmp_dir.mkdir(parents=True, exist_ok=True)
    archive_path = tmp_dir / f'{world}.7z'
    try:
        logger.info('[%s] Téléchargement en cours — %s', world, archive_url)
        progress_state: dict[str, int] = {'last_pct': -1, 'last_bytes': 0}
        with requests.get(
            archive_url, stream=True, timeout=120, headers=_OCAP_HTTP_HEADERS
        ) as resp:
            resp.raise_for_status()
            total_size = expected_size
            content_length = resp.headers.get('Content-Length')
            if content_length and str(content_length).isdigit():
                total_size = int(content_length)
            with open(archive_path, 'wb') as fh:
                downloaded = 0
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        fh.write(chunk)
                        downloaded += len(chunk)
                        _log_download_progress(world, downloaded, total_size, progress_state)
        logger.info(
            '[%s] Téléchargement terminé (%s)',
            world,
            _human_size(archive_path.stat().st_size if archive_path.is_file() else None),
        )

        extract_root = tmp_dir / f'{world}_extract'
        if extract_root.exists():
            import shutil
            shutil.rmtree(extract_root, ignore_errors=True)
        extract_root.mkdir(parents=True, exist_ok=True)

        logger.info('[%s] Extraction de l\'archive…', world)
        with py7zr.SevenZipFile(archive_path, mode='r') as zf:
            zf.extractall(path=extract_root)
        logger.info('[%s] Extraction terminée', world)

        # L'archive contient souvent un sous-dossier {worldName}/.
        source = extract_root / world
        if not source.is_dir():
            # Cherche le premier dossier contenant topo/ ou map.json.
            candidates = [p for p in extract_root.iterdir() if p.is_dir()]
            source = candidates[0] if len(candidates) == 1 else extract_root
            for cand in candidates:
                if (cand / 'topo').is_dir() or (cand / 'map.json').is_file():
                    source = cand
                    break

        import shutil
        if dest.exists():
            # Remplace le contenu sauf fichiers de sync.
            for child in dest.iterdir():
                if child.name.startswith('.ocap_'):
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    try:
                        child.unlink()
                    except OSError:
                        pass
        else:
            dest.mkdir(parents=True, exist_ok=True)

        for child in source.iterdir():
            target = dest / child.name
            if child.is_dir():
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)

        if etag:
            etag_file.write_text(etag, encoding='utf-8')

        if not is_local_map_ready(world):
            return {'status': 'error', 'error': 'extraction OK mais tuiles topo introuvables'}
        logger.info('[%s] Sync terminée — pack prêt (%s)', world, dest)
        return {'status': 'ready', 'world': world, 'path': str(dest)}
    except Exception as exc:
        logger.exception('Sync OCAP échouée pour %s', world)
        return {'status': 'error', 'error': str(exc)}
    finally:
        try:
            if archive_path.is_file():
                archive_path.unlink()
        except OSError:
            pass
        try:
            import shutil
            extract_root = tmp_dir / f'{world}_extract'
            if extract_root.exists():
                shutil.rmtree(extract_root, ignore_errors=True)
        except Exception:
            pass


def proxy_maps_cdn_status(world_name: str) -> dict | None:
    """Interroge le maps-cdn distant. None si non configuré / erreur."""
    url = maps_cdn_status_url(world_name)
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return {'status': 'error', 'error': f'CDN status HTTP {resp.status_code}'}
        data = resp.json()
        data['tile_url_local'] = local_tile_url_template(world_name)
        return data
    except Exception as exc:
        return {'status': 'error', 'error': str(exc)}


def proxy_maps_cdn_sync(world_name: str, *, force: bool = False) -> dict | None:
    """Déclenche sync sur le maps-cdn distant. None si non configuré."""
    url = maps_cdn_sync_url()
    if not url:
        return None
    headers = {'Content-Type': 'application/json'}
    secret = ocap_maps_cdn_secret()
    if secret:
        headers['X-Maps-CDN-Secret'] = secret
    try:
        resp = requests.post(
            url,
            json={'world': world_name, 'force': force},
            headers=headers,
            timeout=30,
        )
        if resp.status_code >= 400:
            return {
                'status': 'error',
                'error': f'CDN sync HTTP {resp.status_code}: {resp.text[:200]}',
            }
        data = resp.json()
        data['tile_url_local'] = local_tile_url_template(world_name)
        return data
    except Exception as exc:
        return {'status': 'error', 'error': str(exc)}


def ocap_sync_status(world_name: str) -> dict:
    """Statut OCAP unifié (volume Storm local ou maps-cdn distant)."""
    world = (world_name or '').strip()
    if is_local_map_ready(world):
        return {
            'status': 'ready',
            'world': world,
            'local_ready': True,
            'syncing': False,
            'tile_url_local': local_tile_url_template(world),
        }
    if ocap_maps_cdn_url():
        remote = proxy_maps_cdn_status(world)
        if remote is not None and remote.get('status') != 'error':
            remote.setdefault('tile_url_local', local_tile_url_template(world))
            if remote.get('local_ready') or remote.get('status') == 'ready':
                remote['status'] = 'ready'
                remote['local_ready'] = True
                remote['syncing'] = False
            return remote
    syncing = is_syncing(world)
    last_error = read_sync_error(world)
    if syncing:
        status = 'queued'
    elif last_error:
        status = 'error'
    else:
        status = 'missing'
    payload = {
        'status': status,
        'world': world,
        'local_ready': False,
        'syncing': syncing,
        'tile_url_local': local_tile_url_template(world),
    }
    if last_error:
        payload['error'] = last_error
    return payload


def should_start_ocap_sync(world_name: str) -> bool:
    status = ocap_sync_status(world_name)
    if status.get('local_ready') or status.get('status') == 'ready':
        return False
    if status.get('syncing'):
        return False
    if status.get('status') == 'error':
        return False
    return status.get('status') in ('missing', 'queued')


def maybe_kick_ocap_sync(world_name: str) -> dict | None:
    world = (world_name or '').strip()
    if not world or not should_start_ocap_sync(world):
        return None
    logger.info('[%s] Lancement sync OCAP (kick)', world)
    return start_sync_ocap_world_async(world)


def start_sync_ocap_world_async(world_name: str, *, force: bool = False) -> dict:
    """Démarre une sync (maps-cdn distant si configuré, sinon local Django)."""
    world = (world_name or '').strip()
    remote = proxy_maps_cdn_sync(world, force=force)
    if remote is not None:
        if remote.get('status') == 'queued':
            remote['syncing'] = True
        logger.info(
            '[%s] Sync déléguée au maps-cdn — status=%s syncing=%s',
            world,
            remote.get('status'),
            remote.get('syncing'),
        )
        return remote

    if is_local_map_ready(world) and not force:
        return {'status': 'ready', 'local_ready': True}
    if is_syncing(world):
        logger.info('[%s] Sync déjà en cours (flag local)', world)
        return {'status': 'queued', 'local_ready': False, 'syncing': True}

    dest = world_storage_dir(world)
    dest.mkdir(parents=True, exist_ok=True)
    clear_sync_error(world)
    syncing_flag_path(world).write_text(str(time.time()), encoding='utf-8')
    logger.info('[%s] Sync planifiée (thread local Storm)', world)

    def runner():
        try:
            sync_ocap_world(world, force=force, dry_run=False)
        except Exception as exc:
            logger.exception('Sync async échouée pour %s', world)
            write_sync_error(world, str(exc))

    thread = threading.Thread(target=runner, name=f'ocap-sync-{world}', daemon=True)
    thread.start()
    return {'status': 'queued', 'local_ready': False, 'syncing': True}


def resolve_safe_tile_path(world_name: str, rest: str) -> Path | None:
    """Résout un chemin de tuile locale, protégé contre le path traversal."""
    safe = is_safe_ocap_tile_ref(world_name, rest)
    if not safe:
        return None
    world, rest_norm = safe
    root = world_storage_dir(world).resolve()
    candidates = [rest_norm]
    if rest_norm.startswith('topo/'):
        candidates.append(rest_norm[len('topo/'):])
    elif not rest_norm.startswith('sat/'):
        candidates.append(f'topo/{rest_norm}')
    for candidate in candidates:
        target = (root / candidate).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        if target.is_file():
            return target
    return None
