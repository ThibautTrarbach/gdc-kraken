"""Service HTTP : sync archives OCAP2 + distribution des tuiles."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import config
from . import sync as sync_mod

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('ocap_maps_cdn')

app = FastAPI(
    title='GDC OCAP Maps CDN',
    description=(
        'Récupère les packs .7z OCAP2 et sert les tuiles en miroir local. '
        'Le navigateur peut continuer à hotlinker le CDN OCAP2 en priorité.'
    ),
    version='1.0.0',
)


class SyncBody(BaseModel):
    world: str = Field(..., min_length=1, max_length=128)
    force: bool = False


def _check_secret(secret: str | None) -> None:
    expected = config.SYNC_SECRET
    if not expected:
        return
    if (secret or '') != expected:
        raise HTTPException(status_code=401, detail='Secret maps-cdn invalide')


def _tile_url_template(world: str) -> str:
    base = (config.PUBLIC_BASE_URL or '').rstrip('/')
    rel = sync_mod.tile_url_template_for_world(world)
    # tile_url_template_for_world returns /{world}/...
    if base:
        return f'{base}{rel}'
    return rel


@app.get('/health')
def health():
    root = sync_mod.storage_root()
    worlds = [p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith('.')]
    return {
        'status': 'ok',
        'storage': str(root),
        'worlds_ready': sorted(w for w in worlds if sync_mod.is_ready(w)),
        'ocap_cdn': 'https://maps.ocap2.com',
    }


@app.get('/api/status')
def api_status(world: str):
    w = (world or '').strip()
    if not w:
        raise HTTPException(status_code=400, detail='world requis')
    ready = sync_mod.is_ready(w)
    syncing = sync_mod.is_syncing(w)
    last_error = sync_mod.read_sync_error(w)
    if ready:
        status = 'ready'
    elif syncing:
        status = 'queued'
    elif last_error:
        status = 'error'
    else:
        status = 'missing'
    payload = {
        'status': status,
        'world': w,
        'local_ready': ready,
        'syncing': syncing,
        'tile_url_local': _tile_url_template(w),
    }
    if last_error and not ready:
        payload['error'] = last_error
    return payload


@app.post('/api/sync')
def api_sync(
    body: SyncBody,
    x_maps_cdn_secret: str | None = Header(default=None, alias='X-Maps-CDN-Secret'),
):
    _check_secret(x_maps_cdn_secret)
    world = body.world.strip()
    logger.info('POST /api/sync world=%s force=%s', world, body.force)
    result = sync_mod.start_sync_async(world, force=body.force)
    result['world'] = world
    result['local_ready'] = sync_mod.is_ready(world)
    result['syncing'] = sync_mod.is_syncing(world)
    result['tile_url_local'] = _tile_url_template(world)
    return result


@app.get('/api/worlds')
def api_worlds():
    """Proxy léger du catalogue OCAP worlds.json (pour debug / clients)."""
    import requests
    try:
        resp = requests.get(
            config.WORLDS_URL,
            timeout=30,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
                ),
                'Accept': 'application/json,text/plain,*/*',
                'Referer': 'https://maps.ocap2.com/',
            },
        )
        resp.raise_for_status()
        return JSONResponse(content=resp.json())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get('/{world}/{rest:path}')
def serve_tile(world: str, rest: str):
    w = (world or '').strip()
    if w and not sync_mod.is_ready(w):
        raise HTTPException(status_code=404, detail='Pack non synchronisé pour ce world')
    target = sync_mod.resolve_tile(world, rest)
    if target is None:
        raise HTTPException(status_code=404, detail='Tuile introuvable')
    content_type = 'application/octet-stream'
    lower = target.name.lower()
    if lower.endswith('.png'):
        content_type = 'image/png'
    elif lower.endswith('.jpg') or lower.endswith('.jpeg'):
        content_type = 'image/jpeg'
    elif lower.endswith('.json'):
        content_type = 'application/json'
    return FileResponse(
        target,
        media_type=content_type,
        headers={'Cache-Control': 'public, max-age=604800'},
    )
