"""Rate limiting léger via cache Django (sans dépendance externe)."""

from __future__ import annotations

import time

from django.core.cache import cache
from django.http import HttpResponse, JsonResponse


def client_ip(request) -> str:
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or 'unknown'


def is_rate_limited(scope: str, identifier: str, limit: int, window_seconds: int) -> bool:
    """Retourne True si la limite est dépassée."""
    cache_key = f'gdc_rl:{scope}:{identifier}'
    now = time.time()
    bucket = cache.get(cache_key)
    if not bucket or now - bucket['start'] >= window_seconds:
        bucket = {'count': 0, 'start': now}
    bucket['count'] += 1
    cache.set(cache_key, bucket, timeout=window_seconds)
    return bucket['count'] > limit


class RateLimitMiddleware:
    """Limite login, API token et sync OCAP POST par adresse IP."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ip = client_ip(request)
        path = request.path

        if request.method == 'POST' and path == '/login/':
            if is_rate_limited('login', ip, limit=20, window_seconds=60):
                return HttpResponse(
                    'Trop de tentatives de connexion. Réessayez dans une minute.',
                    status=429,
                )

        if request.method == 'POST' and path.startswith('/api/'):
            if is_rate_limited('api', ip, limit=120, window_seconds=60):
                return JsonResponse(
                    {'success': False, 'error': 'Trop de requêtes API.'},
                    status=429,
                )

        if request.method == 'POST' and path == '/maps/ocap/sync/':
            if is_rate_limited('ocap_sync', ip, limit=15, window_seconds=60):
                return JsonResponse(
                    {'status': 'error', 'error': 'Trop de synchronisations cartes.'},
                    status=429,
                )

        return self.get_response(request)
