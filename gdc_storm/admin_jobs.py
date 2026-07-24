"""Jobs admin de correction PBO : runners partagés + exécution async (1 job à la fois)."""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any, Callable

from django.conf import settings

from gdc_storm.models import Mission
from gdc_storm.pbo_extract import (
    delete_markers_file,
    extract_markers_from_pbo,
    markers_abs_path,
    save_markers_to_storage,
)

JOB_TYPE_REEXTRACT = 'reextract'
JOB_TYPE_EXTRACT_MARKERS = 'extract_markers'
ALLOWED_JOB_TYPES = frozenset({JOB_TYPE_REEXTRACT, JOB_TYPE_EXTRACT_MARKERS})

_MAX_LOG_LINES = 200

_lock = threading.Lock()
_current_job: dict[str, Any] | None = None


def _default_log(line: str) -> None:
    pass


def _mission_queryset(*, mission_id=None, map_code=None, repair_missing=False):
    qs = Mission.objects.all().order_by('id')
    if mission_id is not None:
        qs = qs.filter(id=mission_id)
    if map_code:
        qs = qs.filter(map__iexact=str(map_code).strip())
    if repair_missing:
        qs = qs.exclude(markers_file='')
    return qs


def run_reextract_missions(
    *,
    dry_run: bool = False,
    mission_id: int | None = None,
    map_code: str | None = None,
    briefing_only: bool = False,
    markers_only: bool = False,
    loadscreen_only: bool = False,
    with_meta: bool = False,
    log: Callable[[str], None] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Régénère briefing / loadScreen / marqueurs / meta depuis les PBO stockés."""
    from yapbol import PBOFile

    from gdc_storm.views import (
        mission_pbo_candidate_names,
        reextract_mission_content_from_pbo,
        resolve_pbo_on_disk,
    )

    write = log or _default_log
    only_flags = [briefing_only, markers_only, loadscreen_only]
    if sum(1 for f in only_flags if f) > 1:
        raise ValueError(
            'Utiliser au plus un de briefing_only / markers_only / loadscreen_only.'
        )

    if briefing_only:
        do_briefing, do_markers, do_loadscreen = True, False, False
    elif markers_only:
        do_briefing, do_markers, do_loadscreen = False, True, False
    elif loadscreen_only:
        do_briefing, do_markers, do_loadscreen = False, False, True
    else:
        do_briefing, do_markers, do_loadscreen = True, True, True

    qs = _mission_queryset(mission_id=mission_id, map_code=map_code)
    total = qs.count()
    processed = updated = skipped = errors = 0
    mode = 'DRY-RUN' if dry_run else 'APPLY'
    parts = []
    if do_briefing:
        parts.append('briefing')
    if do_loadscreen:
        parts.append('loadScreen')
    if do_markers:
        parts.append('marqueurs')
    if with_meta:
        parts.append('meta')
    write(f'[{mode}] {total} mission(s) — {", ".join(parts)}')

    def _emit():
        if on_progress:
            on_progress({
                'processed': processed,
                'total': total,
                'updated': updated,
                'skipped': skipped,
                'errors': errors,
            })

    _emit()
    storage_root = settings.MISSIONS_PBO_STORAGE_PATH
    for mission in qs.iterator():
        processed += 1
        label = f'id={mission.id} {mission.name!r} map={mission.map!r}'

        pbo_name = resolve_pbo_on_disk(mission_pbo_candidate_names(mission))
        if not pbo_name:
            skipped += 1
            write(f'  skip (PBO absent): {label}')
            _emit()
            continue

        pbo_path = os.path.join(storage_root, pbo_name)
        try:
            pbo = PBOFile.read_file(pbo_path)
        except Exception as exc:
            errors += 1
            write(f'  erreur lecture PBO: {label} — {exc}')
            _emit()
            continue

        try:
            summary, notes = reextract_mission_content_from_pbo(
                mission,
                pbo,
                briefing=do_briefing,
                markers=do_markers,
                loadscreen=do_loadscreen,
                meta=with_meta,
                dry_run=dry_run,
            )
        except Exception as exc:
            errors += 1
            write(f'  erreur reextract: {label} — {exc}')
            _emit()
            continue

        updated += 1
        detail_bits = []
        if summary.get('briefing_items') is not None:
            detail_bits.append(f"briefing={summary['briefing_items']}")
        if summary.get('briefing_images') is not None:
            detail_bits.append(f"imgs={summary['briefing_images']}")
        if summary.get('loadscreen') is not None:
            ls = summary['loadscreen']
            detail_bits.append(f"loadScreen={'oui' if ls and ls != 'none' else 'non'}")
        if summary.get('markers') is not None:
            detail_bits.append(f"marqueurs={summary['markers']}")
        if summary.get('meta'):
            detail_bits.append('meta')
        detail = ', '.join(detail_bits) or 'ok'
        verb = 'would' if dry_run else 'ok'
        write(f'  {verb}: {detail}: {label} (pbo={pbo_name})')
        for note in notes[:5]:
            write(f'         note: {note}')
        _emit()

    write(
        f'[{mode}] traitees={processed} mises_a_jour={updated} '
        f'ignorees={skipped} erreurs={errors}'
    )
    result = {
        'processed': processed,
        'total': total,
        'updated': updated,
        'skipped': skipped,
        'errors': errors,
    }
    _emit()
    return result


def run_extract_mission_markers(
    *,
    dry_run: bool = False,
    mission_id: int | None = None,
    map_code: str | None = None,
    force: bool = False,
    repair_missing: bool = False,
    log: Callable[[str], None] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Extrait les marqueurs éditeur depuis les PBO et écrit markers_file."""
    from yapbol import PBOFile

    from gdc_storm.views import mission_pbo_candidate_names, resolve_pbo_on_disk

    write = log or _default_log
    force = force or repair_missing
    qs = _mission_queryset(
        mission_id=mission_id,
        map_code=map_code,
        repair_missing=repair_missing,
    )
    total = qs.count()
    processed = updated = skipped = errors = 0
    mode = 'DRY-RUN' if dry_run else 'APPLY'
    write(f'[{mode}] {total} mission(s) a traiter.')

    def _emit():
        if on_progress:
            on_progress({
                'processed': processed,
                'total': total,
                'updated': updated,
                'skipped': skipped,
                'errors': errors,
            })

    _emit()
    storage_root = settings.MISSIONS_PBO_STORAGE_PATH
    for mission in qs.iterator():
        processed += 1
        label = f'id={mission.id} {mission.name!r} map={mission.map!r}'

        if repair_missing and mission.markers_file:
            if markers_abs_path(mission.markers_file).is_file():
                skipped += 1
                write(f'  skip (fichier OK): {label}')
                _emit()
                continue
            write(f'  repair (JSON absent): {label} ({mission.markers_file})')

        if mission.markers_file and not force:
            skipped += 1
            write(f'  skip (deja extrait): {label}')
            _emit()
            continue

        pbo_name = resolve_pbo_on_disk(mission_pbo_candidate_names(mission))
        if not pbo_name:
            skipped += 1
            write(f'  skip (PBO absent): {label}')
            _emit()
            continue

        pbo_path = os.path.join(storage_root, pbo_name)
        try:
            pbo = PBOFile.read_file(pbo_path)
        except Exception as exc:
            errors += 1
            write(f'  erreur lecture PBO: {label} — {exc}')
            _emit()
            continue

        markers, problems = extract_markers_from_pbo(pbo)
        if problems and not markers:
            skipped += 1
            reason = problems[0]
            write(f'  skip ({reason}): {label}')
            if len(problems) > 1:
                for extra in problems[1:3]:
                    write(f'         {extra}')
            if force and not dry_run:
                delete_markers_file(mission.markers_file)
                mission.markers_file = ''
                mission.save(update_fields=['markers_file'])
                write(f'  force: markers_file vide pour {label}')
            _emit()
            continue

        old_path = mission.markers_file or ''
        path = save_markers_to_storage(markers, mission_id=mission.id) if markers else None
        rel_path = path or ''

        if dry_run:
            write(
                f'  would write {len(markers)} marqueur(s) -> {rel_path or "(vide)"}: {label}'
            )
            if problems:
                for note in problems[:3]:
                    write(f'         note: {note}')
            updated += 1
            _emit()
            continue

        if old_path and old_path != rel_path:
            delete_markers_file(old_path)
        elif not rel_path and old_path:
            delete_markers_file(old_path)

        mission.markers_file = rel_path
        mission.save(update_fields=['markers_file'])
        updated += 1
        if markers:
            abs_written = markers_abs_path(rel_path)
            if not abs_written.is_file():
                errors += 1
                write(f'  erreur ecriture disque: {label} ({abs_written})')
                _emit()
                continue
            write(f'  ok: {len(markers)} marqueur(s) -> {rel_path}: {label}')
            write(f'         disk: {abs_written}')
        else:
            write(f'  ok: 0 marqueur(s) -> (vide): {label}')
        if problems:
            for note in problems[:3]:
                write(f'         note: {note}')
        _emit()

    write(
        f'[{mode}] traitees={processed} mises_a_jour={updated} '
        f'ignorees={skipped} erreurs={errors}'
    )
    result = {
        'processed': processed,
        'total': total,
        'updated': updated,
        'skipped': skipped,
        'errors': errors,
    }
    _emit()
    return result


def _job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    return {
        'job_id': job['job_id'],
        'job_type': job['job_type'],
        'status': job['status'],
        'options': dict(job.get('options') or {}),
        'processed': job.get('processed', 0),
        'total': job.get('total', 0),
        'updated': job.get('updated', 0),
        'skipped': job.get('skipped', 0),
        'errors': job.get('errors', 0),
        'lines': list(job.get('lines') or []),
        'started_at': job.get('started_at'),
        'finished_at': job.get('finished_at'),
        'error': job.get('error'),
    }


def get_correction_job(job_id: str | None = None) -> dict[str, Any] | None:
    with _lock:
        if _current_job is None:
            return None
        if job_id is not None and _current_job['job_id'] != job_id:
            return None
        return _job_snapshot(_current_job)


def _append_log(job: dict[str, Any], line: str) -> None:
    lines = job.setdefault('lines', [])
    lines.append(line)
    if len(lines) > _MAX_LOG_LINES:
        del lines[:-_MAX_LOG_LINES]


def _update_progress(job: dict[str, Any], counters: dict[str, Any]) -> None:
    with _lock:
        for key in ('processed', 'total', 'updated', 'skipped', 'errors'):
            if key in counters:
                job[key] = counters[key]


def _run_job(job: dict[str, Any]) -> None:
    job_type = job['job_type']
    options = job.get('options') or {}

    def log(line: str) -> None:
        with _lock:
            _append_log(job, line)

    def on_progress(counters: dict[str, Any]) -> None:
        _update_progress(job, counters)

    with _lock:
        job['status'] = 'running'
        job['started_at'] = time.time()

    try:
        if job_type == JOB_TYPE_REEXTRACT:
            result = run_reextract_missions(
                dry_run=bool(options.get('dry_run')),
                mission_id=options.get('mission_id'),
                map_code=options.get('map') or None,
                briefing_only=bool(options.get('briefing_only')),
                markers_only=bool(options.get('markers_only')),
                loadscreen_only=bool(options.get('loadscreen_only')),
                with_meta=bool(options.get('with_meta')),
                log=log,
                on_progress=on_progress,
            )
        elif job_type == JOB_TYPE_EXTRACT_MARKERS:
            result = run_extract_mission_markers(
                dry_run=bool(options.get('dry_run')),
                mission_id=options.get('mission_id'),
                map_code=options.get('map') or None,
                force=bool(options.get('force')),
                repair_missing=bool(options.get('repair_missing')),
                log=log,
                on_progress=on_progress,
            )
        else:
            raise ValueError(f'Type de job inconnu: {job_type}')

        with _lock:
            job['status'] = 'done'
            job['finished_at'] = time.time()
            for key in ('processed', 'total', 'updated', 'skipped', 'errors'):
                job[key] = result.get(key, job.get(key, 0))
    except Exception as exc:
        with _lock:
            job['status'] = 'error'
            job['finished_at'] = time.time()
            job['error'] = str(exc)
            _append_log(job, f'ERREUR: {exc}')


def start_correction_job(job_type: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Démarre un job en arrière-plan. Refuse si un job est déjà actif."""
    global _current_job

    if job_type not in ALLOWED_JOB_TYPES:
        raise ValueError(f'Type de job invalide: {job_type}')

    options = dict(options or {})
    with _lock:
        if _current_job is not None and _current_job['status'] in ('queued', 'running'):
            return {
                'ok': False,
                'error': 'Un job est déjà en cours.',
                'job': _job_snapshot(_current_job),
            }

        job = {
            'job_id': str(uuid.uuid4()),
            'job_type': job_type,
            'status': 'queued',
            'options': options,
            'processed': 0,
            'total': 0,
            'updated': 0,
            'skipped': 0,
            'errors': 0,
            'lines': [],
            'started_at': None,
            'finished_at': None,
            'error': None,
        }
        _current_job = job

    thread = threading.Thread(
        target=_run_job,
        args=(job,),
        name=f'admin-correction-{job_type}',
        daemon=True,
    )
    thread.start()
    return {'ok': True, 'job': _job_snapshot(job)}


def reset_correction_job_for_tests() -> None:
    """Réinitialise l'état global (tests uniquement)."""
    global _current_job
    with _lock:
        _current_job = None
