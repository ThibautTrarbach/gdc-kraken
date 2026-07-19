"""Helpers de calcul de statistiques locales (pages détail)."""
from collections import defaultdict

from django.db.models import Count, Q

from .models import GameSession, RoleCategory


def session_win_rate(sessions_qs):
    """
    Stats de verdicts sur un queryset GameSession.
    Retourne succes, echec, decisive, win_rate, win_rate_display, verdict_counts, sessions_count.
    """
    sessions_count = sessions_qs.count() if hasattr(sessions_qs, 'count') else len(sessions_qs)
    if hasattr(sessions_qs, 'values'):
        raw = {
            row['verdict']: row['c']
            for row in sessions_qs.values('verdict').annotate(c=Count('id'))
        }
    else:
        raw = defaultdict(int)
        for s in sessions_qs:
            raw[getattr(s, 'verdict', None) or GameSession.VERDICT_INCONNU] += 1
        raw = dict(raw)

    succes = raw.get(GameSession.VERDICT_SUCCES, 0)
    echec = raw.get(GameSession.VERDICT_ECHEC, 0)
    decisive = succes + echec
    win_rate = round(100 * succes / decisive) if decisive else None
    if win_rate is not None:
        win_rate_display = f'{win_rate} % ({succes}/{decisive})'
    else:
        win_rate_display = '—'

    labels = dict(GameSession.VERDICT_CHOICES)
    verdict_counts = [
        {'verdict': k, 'label': labels.get(k, k), 'count': v}
        for k, v in sorted(raw.items(), key=lambda x: -x[1])
    ]
    return {
        'sessions_count': sessions_count,
        'succes': succes,
        'echec': echec,
        'decisive': decisive,
        'win_rate': win_rate,
        'win_rate_display': win_rate_display,
        'verdict_counts': verdict_counts,
    }


def player_survival_stats(status_by_session):
    """
    À partir d'un mapping session_id -> status (VIVANT/MORT), une entrée par session.
    Retourne alive, dead, ratio, ratio_display, survival_pct.
    """
    alive, dead = 0, 0
    for status in status_by_session.values():
        if status == 'MORT':
            dead += 1
        else:
            alive += 1
    total = alive + dead
    if dead > 0:
        ratio = round(alive / dead, 2)
        ratio_display = str(ratio)
    elif alive > 0:
        ratio = float('inf')
        ratio_display = 'inf.'
    else:
        ratio = None
        ratio_display = '—'
    survival_pct = round(100 * alive / total) if total else None
    return {
        'alive': alive,
        'dead': dead,
        'ratio': ratio_display if ratio != float('inf') else 'inf.',
        'ratio_display': ratio_display,
        'survival_pct': survival_pct,
    }


def role_breakdown(gsp_qs, limit=5):
    """Top rôles bruts + top catégories (via RoleCategory) sur un queryset GameSessionPlayer."""
    top_roles_raw = (
        gsp_qs.exclude(role='')
        .values('role')
        .annotate(c=Count('id'))
        .order_by('-c')[:limit]
    )
    top_roles = [{'role': r['role'], 'count': r['c']} for r in top_roles_raw]

    role_cat_map = {
        rc.role_name: rc.category for rc in RoleCategory.objects.all()
    }
    category_counts = defaultdict(int)
    for r in gsp_qs.exclude(role='').values('role').annotate(c=Count('id')):
        cat = role_cat_map.get(r['role'], r['role'])
        category_counts[cat] += r['c']
    top_categories = sorted(
        [{'category': k, 'count': v} for k, v in category_counts.items()],
        key=lambda x: -x['count'],
    )[:limit]
    return {
        'top_roles': top_roles,
        'top_categories': top_categories,
    }


def duration_stats(sessions_iterable):
    """
    Durées à partir d'objets GameSession (ou dicts avec start_time/end_time/id/name).
    Retourne avg_min, total_hours, longest {id, name, duration_min} ou None.
    """
    durations = []
    longest = None
    longest_sec = -1
    for s in sessions_iterable:
        start = getattr(s, 'start_time', None) or (s.get('start_time') if isinstance(s, dict) else None)
        end = getattr(s, 'end_time', None) or (s.get('end_time') if isinstance(s, dict) else None)
        if not start or not end:
            continue
        sec = (end - start).total_seconds()
        if sec < 0:
            continue
        durations.append(sec)
        if sec > longest_sec:
            longest_sec = sec
            sid = getattr(s, 'id', None) or (s.get('id') if isinstance(s, dict) else None)
            name = getattr(s, 'name', None) or (s.get('name') if isinstance(s, dict) else None)
            longest = {
                'id': sid,
                'name': name,
                'duration_min': int(sec // 60),
            }
    if not durations:
        return {
            'avg_min': None,
            'total_hours': 0,
            'longest': None,
        }
    return {
        'avg_min': round(sum(durations) / len(durations) / 60),
        'total_hours': round(sum(durations) / 3600, 1),
        'longest': longest,
    }


def avg_players_survivors(sessions_with_counts):
    """
    Moyenne joueurs / survivants.
    sessions_with_counts: iterable d'objets/dicts avec players_count et vivant_count.
    """
    players = []
    vivants = []
    for s in sessions_with_counts:
        pc = getattr(s, 'players_count', None)
        if pc is None and isinstance(s, dict):
            pc = s.get('players_count')
        vc = getattr(s, 'vivant_count', None)
        if vc is None and isinstance(s, dict):
            vc = s.get('vivant_count')
        if pc is not None:
            players.append(pc)
        if vc is not None:
            vivants.append(vc)
    return {
        'avg_players': round(sum(players) / len(players), 1) if players else None,
        'avg_survivors': round(sum(vivants) / len(vivants), 1) if vivants else None,
    }


def top_players_for_gsp(gsp_qs, limit=5):
    """Top joueurs par occurrences dans un queryset GameSessionPlayer."""
    rows = (
        gsp_qs.values('player_id', 'player__name')
        .annotate(c=Count('id'))
        .order_by('-c')[:limit]
    )
    return [
        {'id': r['player_id'], 'name': r['player__name'], 'count': r['c']}
        for r in rows
    ]


def top_maps_for_sessions(sessions_qs, map_display_fn, limit=5):
    """Top cartes jouées à partir d'un queryset GameSession."""
    rows = (
        sessions_qs.values('map')
        .annotate(c=Count('id'))
        .order_by('-c')[:limit]
    )
    return [
        {
            'code': r['map'] or '',
            'display': map_display_fn(r['map']) if r['map'] else '—',
            'count': r['c'],
        }
        for r in rows
    ]


def top_missions_for_sessions(sessions_qs, limit=5):
    """Top missions liées (FK) les plus jouées, avec win rate."""
    rows = (
        sessions_qs.filter(mission__isnull=False)
        .values('mission_id', 'mission__name')
        .annotate(
            c=Count('id'),
            succes=Count('id', filter=Q(verdict=GameSession.VERDICT_SUCCES)),
            echec=Count('id', filter=Q(verdict=GameSession.VERDICT_ECHEC)),
        )
        .order_by('-c')[:limit]
    )
    result = []
    for r in rows:
        decisive = r['succes'] + r['echec']
        wr = round(100 * r['succes'] / decisive) if decisive else None
        result.append({
            'id': r['mission_id'],
            'name': r['mission__name'],
            'count': r['c'],
            'win_rate': wr,
            'win_rate_display': f'{wr} %' if wr is not None else '—',
        })
    return result


def published_missions_win_rate(missions_qs):
    """Win rate agrégé sur les sessions des missions publiées par un user."""
    mission_ids = list(missions_qs.values_list('id', flat=True))
    if not mission_ids:
        return session_win_rate(GameSession.objects.none())
    return session_win_rate(GameSession.objects.filter(mission_id__in=mission_ids))
