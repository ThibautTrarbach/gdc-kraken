"""Configuration du service de distribution des cartes OCAP."""

from __future__ import annotations

import os
from pathlib import Path


def env(name: str, default: str = '') -> str:
    return os.environ.get(name, default).strip()


HOST = env('MAPS_CDN_HOST', '0.0.0.0')
PORT = int(env('MAPS_CDN_PORT', '8080') or '8080')
STORAGE_PATH = Path(env('MAPS_CDN_STORAGE_PATH', '/data/ocap_maps'))
WORLDS_URL = env('OCAP_WORLDS_URL', 'https://maps.ocap2.com/worlds.json')
ARCHIVES_LIST_URL = env('OCAP_ARCHIVES_LIST_URL', 'https://archives.ocap2.com/list')
# Secret optionnel pour POST /api/sync (header X-Maps-CDN-Secret).
SYNC_SECRET = env('MAPS_CDN_SECRET', '')
PUBLIC_BASE_URL = env('MAPS_CDN_PUBLIC_BASE_URL', '').rstrip('/')
