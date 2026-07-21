"""Sync archives OCAP2 (.7z) vers le stockage local du maps-cdn."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path

import requests

from . import config

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


_OCAP_HTTP_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json,application/octet-stream,*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://maps.ocap2.com/',
}

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def storage_root() -> Path:
    root = Path(config.STORAGE_PATH)
    root.mkdir(parents=True, exist_ok=True)
    return root


def world_dir(world: str) -> Path:
    safe = (world or '').strip().replace('\\', '/').split('/')[-1]
    return storage_root() / safe


def is_ready(world: str) -> bool:
    root = world_dir(world)
    topo = root / 'topo'
    if topo.is_dir() and any(topo.rglob('*.png')):
        return True
    return root.is_dir() and any(root.glob('*/*/*.png'))


def syncing_flag(world: str) -> Path:
    return world_dir(world) / '.ocap_syncing'


def sync_error_file(world: str) -> Path:
    return world_dir(world) / '.ocap_sync_error'


def read_sync_error(world: str) -> str | None:
    path = sync_error_file(world)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding='utf-8').strip()
    except OSError:
        return None
    return text or None


def write_sync_error(world: str, message: str) -> None:
    path = sync_error_file(world)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((message or 'sync échouée').strip(), encoding='utf-8')


def clear_sync_error(world: str) -> None:
    path = sync_error_file(world)
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def etag_file(world: str) -> Path:
    return world_dir(world) / '.ocap_etag'


def is_syncing(world: str) -> bool:
    flag = syncing_flag(world)
    if not flag.is_file():
        return False
    try:
        age = time.time() - flag.stat().st_mtime
    except OSError:
        return False
    if age > 7200:
        try:
            flag.unlink()
        except OSError:
            pass
        return False
    return True


def fetch_archives() -> dict[str, dict]:
    resp = requests.get(config.ARCHIVES_LIST_URL, timeout=60, headers=_OCAP_HTTP_HEADERS)
    resp.raise_for_status()
    payload = resp.json()
    index: dict[str, dict] = {}
    if isinstance(payload, dict):
        for key, item in payload.items():
            if not isinstance(item, dict):
                continue
            name = (item.get('worldName') or key or '').strip().lower()
            if name:
                index[name] = item
    elif isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = (item.get('worldName') or '').strip().lower()
            if name:
                index[name] = item
    return index


def _lock_for(world: str) -> threading.Lock:
    key = world.lower()
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def resolve_tile(world: str, rest: str) -> Path | None:
    w = (world or '').strip()
    if not w or '/' in w or '\\' in w or w.startswith('.'):
        return None
    rest_norm = (rest or '').replace('\\', '/').lstrip('/')
    if not rest_norm or '..' in rest_norm.split('/'):
        return None
    root = world_dir(w).resolve()
    candidates = [rest_norm]
    # Certains packs OCAP ont les zooms à la racine (sans sous-dossier topo/).
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


def tile_url_template_for_world(world: str) -> str:
    """Choisit topo/ ou racine selon le layout réellement extrait."""
    root = world_dir(world)
    topo = root / 'topo'
    if topo.is_dir() and any(topo.rglob('*.png')):
        return f'/{world}/topo/{{z}}/{{x}}/{{y}}.png'
    if root.is_dir() and any(root.glob('*/*/*.png')):
        return f'/{world}/{{z}}/{{x}}/{{y}}.png'
    return f'/{world}/topo/{{z}}/{{x}}/{{y}}.png'


def sync_world(world_name: str, *, force: bool = False) -> dict:
    world = (world_name or '').strip()
    if not world or '/' in world or '\\' in world or '..' in world:
        return {'status': 'error', 'error': 'world invalide'}

    try:
        archives = fetch_archives()
    except Exception as exc:
        return {'status': 'error', 'error': f'catalogue archives: {exc}'}

    entry = archives.get(world.lower())
    if not entry:
        return {'status': 'error', 'error': f'archive absente pour {world}'}

    archive_url = entry.get('url')
    etag = str(entry.get('etag') or '')
    archive_size = entry.get('size')
    if not archive_url:
        return {'status': 'error', 'error': 'url archive manquante'}

    dest = world_dir(world)
    etag_path = etag_file(world)
    if is_ready(world) and not force:
        if etag and etag_path.is_file() and etag_path.read_text(encoding='utf-8').strip() == etag:
            logger.info('[%s] Sync ignorée — etag inchangé', world)
            return {'status': 'ready', 'skipped': True, 'reason': 'etag inchangé'}
        logger.info('[%s] Sync ignorée — pack déjà présent', world)
        return {'status': 'ready', 'skipped': True, 'reason': 'déjà présent'}

    lock = _lock_for(world)
    if not lock.acquire(blocking=False):
        logger.info('[%s] Sync déjà en cours (lock)', world)
        return {'status': 'queued', 'reason': 'sync déjà en cours'}

    flag = syncing_flag(world)
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
            world, archive_url, etag, dest, etag_path, expected_size=archive_size
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
    etag_path: Path,
    *,
    expected_size: int | None = None,
) -> dict:
    try:
        import py7zr
    except ImportError:
        return {'status': 'error', 'error': 'py7zr non installé'}

    tmp_dir = storage_root() / '.tmp'
    tmp_dir.mkdir(parents=True, exist_ok=True)
    archive_path = tmp_dir / f'{world}.7z'
    extract_root = tmp_dir / f'{world}_extract'
    try:
        logger.info('[%s] Téléchargement en cours — %s', world, archive_url)
        progress_state: dict[str, int] = {'last_pct': -1, 'last_bytes': 0}
        with requests.get(
            archive_url, stream=True, timeout=180, headers=_OCAP_HTTP_HEADERS
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

        if extract_root.exists():
            shutil.rmtree(extract_root, ignore_errors=True)
        extract_root.mkdir(parents=True, exist_ok=True)

        logger.info('[%s] Extraction de l\'archive…', world)
        with py7zr.SevenZipFile(archive_path, mode='r') as zf:
            zf.extractall(path=extract_root)
        logger.info('[%s] Extraction terminée', world)

        source = extract_root / world
        if not source.is_dir():
            candidates = [p for p in extract_root.iterdir() if p.is_dir()]
            source = candidates[0] if len(candidates) == 1 else extract_root
            for cand in candidates:
                if (cand / 'topo').is_dir() or (cand / 'map.json').is_file():
                    source = cand
                    break

        if dest.exists():
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
            etag_path.write_text(etag, encoding='utf-8')

        if not is_ready(world):
            return {'status': 'error', 'error': 'extraction OK mais tuiles topo introuvables'}
        logger.info('[%s] Sync terminée — pack prêt (%s)', world, dest)
        return {'status': 'ready', 'world': world, 'path': str(dest)}
    except Exception as exc:
        logger.exception('Sync échouée pour %s', world)
        return {'status': 'error', 'error': str(exc)}
    finally:
        try:
            if archive_path.is_file():
                archive_path.unlink()
        except OSError:
            pass
        if extract_root.exists():
            shutil.rmtree(extract_root, ignore_errors=True)


def start_sync_async(world: str, *, force: bool = False) -> dict:
    if is_ready(world) and not force:
        return {'status': 'ready', 'local_ready': True}
    if is_syncing(world):
        logger.info('[%s] Sync déjà en cours (flag)', world)
        return {'status': 'queued', 'local_ready': False, 'syncing': True}

    dest = world_dir(world)
    dest.mkdir(parents=True, exist_ok=True)
    clear_sync_error(world)
    syncing_flag(world).write_text(str(time.time()), encoding='utf-8')
    logger.info('[%s] Sync planifiée (thread async)', world)

    def runner():
        try:
            sync_world(world, force=force)
        except Exception as exc:
            logger.exception('Sync async échouée pour %s', world)
            write_sync_error(world, str(exc))

    threading.Thread(target=runner, name=f'maps-sync-{world}', daemon=True).start()
    return {'status': 'queued', 'local_ready': False, 'syncing': True}
