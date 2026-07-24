"""Vues admin : lancement des scripts de correction PBO en arrière-plan."""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from gdc_storm.admin_jobs import (
    ALLOWED_JOB_TYPES,
    JOB_TYPE_EXTRACT_MARKERS,
    JOB_TYPE_REEXTRACT,
    get_correction_job,
    start_correction_job,
)
from gdc_storm.views import _require_superuser


def _parse_json_body(request) -> dict:
    if not request.body:
        return {}
    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _optional_int(value):
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_options(job_type: str, raw: dict) -> dict | str:
    """Retourne un dict d'options valides, ou un message d'erreur str."""
    dry_run = bool(raw.get('dry_run', True))
    mission_id = _optional_int(raw.get('mission_id'))
    if raw.get('mission_id') not in (None, '') and mission_id is None:
        return 'mission_id invalide.'
    map_code = (raw.get('map') or '').strip() or None

    if job_type == JOB_TYPE_REEXTRACT:
        briefing_only = bool(raw.get('briefing_only'))
        markers_only = bool(raw.get('markers_only'))
        loadscreen_only = bool(raw.get('loadscreen_only'))
        if sum(1 for f in (briefing_only, markers_only, loadscreen_only) if f) > 1:
            return 'Choisir au plus un mode : briefing-only / markers-only / loadscreen-only.'
        return {
            'dry_run': dry_run,
            'mission_id': mission_id,
            'map': map_code,
            'briefing_only': briefing_only,
            'markers_only': markers_only,
            'loadscreen_only': loadscreen_only,
            'with_meta': bool(raw.get('with_meta')),
        }

    if job_type == JOB_TYPE_EXTRACT_MARKERS:
        return {
            'dry_run': dry_run,
            'mission_id': mission_id,
            'map': map_code,
            'force': bool(raw.get('force')),
            'repair_missing': bool(raw.get('repair_missing')),
        }

    return f'Type de job inconnu: {job_type}'


@login_required
def admin_corrections(request):
    forbidden = _require_superuser(request)
    if forbidden:
        return forbidden
    return render(request, 'gdc_storm/admin_corrections.html', {
        'current_job': get_correction_job(),
    })


@login_required
@require_POST
def admin_corrections_run(request):
    forbidden = _require_superuser(request)
    if forbidden:
        return JsonResponse({'ok': False, 'error': 'Accès réservé aux administrateurs.'}, status=403)

    body = _parse_json_body(request)
    job_type = (body.get('job_type') or '').strip()
    if job_type not in ALLOWED_JOB_TYPES:
        return JsonResponse(
            {'ok': False, 'error': f'job_type invalide (attendu: {", ".join(sorted(ALLOWED_JOB_TYPES))}).'},
            status=400,
        )

    options = _normalize_options(job_type, body.get('options') or body)
    if isinstance(options, str):
        return JsonResponse({'ok': False, 'error': options}, status=400)

    try:
        result = start_correction_job(job_type, options)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    status = 409 if not result.get('ok') else 200
    return JsonResponse(result, status=status)


@login_required
@require_GET
def admin_corrections_status(request):
    forbidden = _require_superuser(request)
    if forbidden:
        return JsonResponse({'ok': False, 'error': 'Accès réservé aux administrateurs.'}, status=403)

    job_id = (request.GET.get('job_id') or '').strip() or None
    job = get_correction_job(job_id)
    if job is None:
        return JsonResponse({'ok': True, 'job': None})
    return JsonResponse({'ok': True, 'job': job})
