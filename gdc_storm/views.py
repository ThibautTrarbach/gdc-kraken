from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST


@login_required
def change_password(request):
    user = request.user
    has_password = user.has_usable_password()

    if request.method == 'POST':
        old_password = request.POST.get('old_password')
        new_password1 = request.POST.get('new_password1')
        new_password2 = request.POST.get('new_password2')

        if has_password and not user.check_password(old_password or ''):
            messages.error(request, "Ancien mot de passe incorrect.")
        elif new_password1 != new_password2:
            messages.error(request, "Les nouveaux mots de passe ne correspondent pas.")
        else:
            from django.contrib.auth.password_validation import validate_password
            from django.core.exceptions import ValidationError
            try:
                validate_password(new_password1, user=user)
            except ValidationError as exc:
                for msg in exc.messages:
                    messages.error(request, msg)
            else:
                user.set_password(new_password1)
                user.save()
                update_session_auth_hash(request, user)
                messages.success(
                    request,
                    "Mot de passe défini avec succès." if not has_password else "Mot de passe modifié avec succès.",
                )
                return redirect('home')

    return render(request, 'gdc_storm/change_password.html', {
        'has_usable_password': has_password,
    })


def pending_approval(request):
    """Page affichée après création d'un compte social non encore validé."""
    return render(request, 'gdc_storm/pending_approval.html')


@login_required
def account_connections(request):
    from allauth.socialaccount.models import SocialAccount

    from gdc_storm.social_auth import (
        get_social_auth_enabled,
        provider_label,
    )

    linked = {
        sa.provider: sa
        for sa in SocialAccount.objects.filter(user=request.user)
    }
    enabled = get_social_auth_enabled()
    providers = []
    for provider_id in ("discord", "steam"):
        account = linked.get(provider_id)
        is_enabled = enabled.get(provider_id, False)
        if not is_enabled and not account:
            continue
        display_name = ""
        if account:
            extra = account.extra_data or {}
            display_name = (
                extra.get("username")
                or extra.get("global_name")
                or extra.get("personaname")
                or extra.get("name")
                or account.uid
            )
        providers.append({
            "id": provider_id,
            "label": provider_label(provider_id),
            "enabled": is_enabled,
            "linked": account is not None,
            "account": account,
            "display_name": display_name,
            "can_disconnect": _can_disconnect_provider(request.user, provider_id, linked),
        })

    return render(request, 'gdc_storm/account_connections.html', {
        'connection_providers': providers,
        'has_usable_password': request.user.has_usable_password(),
    })


def _can_disconnect_provider(user, provider_id, linked_map):
    """Refuse de délier si c'est la dernière méthode d'accès."""
    if provider_id not in linked_map:
        return False
    other_links = [p for p in linked_map if p != provider_id]
    return user.has_usable_password() or bool(other_links)


@login_required
@require_POST
def disconnect_social_account(request, provider):
    from allauth.socialaccount.models import SocialAccount

    from gdc_storm.social_auth import provider_label

    if provider not in ("discord", "steam"):
        messages.error(request, "Service inconnu.")
        return redirect('account_connections')

    linked = {
        sa.provider: sa
        for sa in SocialAccount.objects.filter(user=request.user)
    }
    account = linked.get(provider)
    if not account:
        messages.error(request, "Aucune connexion à délier.")
        return redirect('account_connections')

    if not _can_disconnect_provider(request.user, provider, linked):
        messages.error(
            request,
            "Impossible de délier : définissez d'abord un mot de passe "
            "ou gardez au moins une autre connexion.",
        )
        return redirect('account_connections')

    account.delete()
    messages.success(
        request,
        f"Connexion {provider_label(provider)} déliée.",
    )
    return redirect('account_connections')


import json
import re
import uuid
import os
import logging
import glob
import shutil
import tempfile
import datetime
import time
from collections import defaultdict
import secrets

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q, F
from django.db.models.functions import TruncMonth, ExtractWeekDay, ExtractHour
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.core.files.storage import default_storage
from django.views.decorators.http import require_POST, require_http_methods
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.contrib.auth.models import Group, User
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from functools import wraps
from datetime import timedelta
from calendar import month_abbr

UPLOAD_ANALYZE_MAX_FILES = 100
RECUP_ANALYZE_MAX_FILES = 1500
RECUP_USERNAME = 'GDC-RECUP'
# Date de publication forcée pour les missions créées via /recup/
RECUP_PUBLICATION_DATE = datetime.datetime(2025, 10, 29, 0, 0, 0)
# Temporaire : désactive la validation stricte des noms de fichiers en /recup/
RECUP_RELAX_FILENAME = False

from yapbol import PBOFile


def annotate_session_player_counts(queryset):
    """Ajoute players_count et vivant_count en une seule passe SQL (anti N+1)."""
    return queryset.annotate(
        players_count=Count('players'),
        vivant_count=Count('players', filter=Q(players__status='VIVANT')),
    )


def get_recup_user():
    """Retourne (et crée si besoin) le compte dédié à la récupération des missions."""
    user, created = User.objects.get_or_create(
        username=RECUP_USERNAME,
        defaults={
            'is_active': False,
            'is_staff': False,
            'is_superuser': False,
        },
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=['password'])
    return user

from .models import Mission, MapName, Player, GameSession, GameSessionPlayer, RoleCategory, ApiToken
from .models import LegacyRole, LegacyMission, LegacyImportError, LegacyGameSession, LegacyMapNames, LegacyGameSessionPlayerRole, LegacyPlayers
from .forms import MissionOwnerForm, MissionStatusForm
from gdc_storm.utils import (
    parse_mission_filename,
    parse_mission_filename_lenient,
    recup_parse_mission_filename,
    invalidate_session_list_cache,
    SESSION_LIST_CACHE_KEY,
    STATS_CACHE_KEY,
    ROLE_USAGE_CACHE_KEY,
)
from gdc_storm.stats_helpers import (
    session_win_rate,
    player_survival_stats,
    role_breakdown,
    duration_stats,
    avg_players_survivors,
    top_players_for_gsp,
    top_maps_for_sessions,
    top_missions_for_sessions,
    published_missions_win_rate,
)
from gdc_storm.arma3map import ARMA_MARKER_COLORS, MARKER_ICON_MAP
from gdc_storm.ocap_maps import (
    get_mission_map_config,
    is_local_map_ready,
    is_syncing,
    local_tile_url_template,
    maybe_kick_ocap_sync,
    ocap_maps_cdn_url,
    ocap_sync_status,
    proxy_maps_cdn_status,
    resolve_safe_tile_path,
    start_sync_ocap_world_async,
    fetch_maps_cdn_tile,
)
from gdc_storm.pbo_extract import (
    is_sqm_binarized,
    extract_mission_data_from_pbo,
    extract_briefing_from_pbo,
    extract_markers_from_pbo,
    save_markers_to_storage,
    delete_markers_file,
    get_mission_markers_status,
    load_markers_from_mission,
)
from django.core.paginator import Paginator

STATS_CACHE_TTL = 300
STATS_MIN_SESSIONS_FOR_RATIO = 5
STATS_MIN_DECISIVE_FOR_MAP_WR = 5


# Home page view
def home(request):
    # Statistiques globales
    missions_count = Mission.objects.count()
    sessions_count = GameSession.objects.count()
    # Render the home page with hero section and navigation buttons
    return render(request, 'gdc_storm/home.html', {
        'missions_count': missions_count,
        'sessions_count': sessions_count,
    })

def user_is_mission_maker(user):
    return user.is_authenticated and (user.is_superuser or user.groups.filter(name='Mission Maker').exists())


def user_can_recup(user):
    """Tout utilisateur connecté et actif peut utiliser la récupération."""
    return bool(user and user.is_authenticated and user.is_active)

def clean_temp_files(temp_dir, max_age_seconds=3600):
    """Supprime les fichiers temporaires plus vieux que max_age_seconds dans temp_dir."""
    now = int(time.time())
    for temp_file in glob.glob(os.path.join(temp_dir, '*')):
        try:
            if os.path.isfile(temp_file):
                age = int(os.path.getmtime(temp_file))
                if now - age > max_age_seconds:
                    os.remove(temp_file)
        except Exception as e:
            logging.warning(f"Erreur lors du nettoyage du fichier temporaire {temp_file}: {e}")


def get_upload_temp_dir(user=None):
    """Répertoire temp global, ou sous-dossier par utilisateur (anti-IDOR)."""
    base = os.path.join(tempfile.gettempdir(), 'gdc_storm')
    if user is not None and getattr(user, 'is_authenticated', False) and getattr(user, 'pk', None):
        temp_dir = os.path.join(base, f'u{user.pk}')
    else:
        temp_dir = base
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def is_safe_upload_temp_path(temp_file_path, user=None):
    """Refuse chemins hors du temp user (anti path traversal + IDOR cross-user)."""
    if not temp_file_path or user is None or not getattr(user, 'pk', None):
        return False
    temp_dir = os.path.realpath(get_upload_temp_dir(user))
    real_path = os.path.realpath(temp_file_path)
    try:
        return os.path.commonpath([temp_dir, real_path]) == temp_dir and os.path.isfile(real_path)
    except ValueError:
        return False


def save_uploaded_pbo_to_temp(uploaded_file, user=None):
    """Sauve un UploadedFile dans le répertoire temp et retourne (temp_file_path, temp_file_name, filename)."""
    raw_name = uploaded_file.name or ''
    filename = os.path.basename(raw_name.replace('\\', '/'))
    if not filename or filename in ('.', '..'):
        raise ValueError("Nom de fichier upload invalide.")
    temp_file_name = f"{uuid.uuid4()}_{filename}"
    temp_dir = get_upload_temp_dir(user)
    temp_file_path = os.path.join(temp_dir, temp_file_name)
    if os.path.dirname(os.path.realpath(temp_file_path)) != os.path.realpath(temp_dir):
        raise ValueError("Chemin temporaire hors répertoire autorisé.")
    with open(temp_file_path, 'wb+') as destination:
        for chunk in uploaded_file.chunks():
            destination.write(chunk)
    return temp_file_path, temp_file_name, filename


def safe_internal_redirect(request, candidate, fallback_name):
    """Redirige uniquement vers une URL interne (anti open-redirect)."""
    from django.utils.http import url_has_allowed_host_and_scheme
    if candidate and url_has_allowed_host_and_scheme(
        url=candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(candidate)
    return redirect(fallback_name)


def save_pbo_to_storage(temp_file_path, filename):
    """Déplace un PBO temporaire vers le stockage (compatible cross-device Docker)."""
    dest_dir = settings.MISSIONS_PBO_STORAGE_PATH
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, filename)
    try:
        shutil.move(temp_file_path, dest)
    except PermissionError as e:
        raise PermissionError(
            f"{e} — le processus (PUID/PGID) n'a pas le droit d'écrire dans "
            f"{dest_dir}. Alignez PUID/PGID sur le propriétaire du bind-mount host."
        ) from e
    return dest


def backup_existing_pbo(existing_mission):
    """
    Déplace l'ancien PBO (version courante en DB) vers MISSIONS_PBO_STORAGE_PATH/backup/.
    Non bloquant : absence ou erreur → warning log, retourne None.
    """
    storage_root = settings.MISSIONS_PBO_STORAGE_PATH
    version = str(existing_mission.version).lstrip('Vv')
    name = existing_mission.name
    map_name = (existing_mission.map or '').lower()
    actual_name = resolve_pbo_on_disk(
        mission_pbo_candidate_names(existing_mission),
        storage_root=storage_root,
    )
    if not actual_name:
        logging.warning(
            "Aucun PBO à archiver pour %s V%s (%s) dans %s",
            name, version, map_name, storage_root,
        )
        return None
    old_path = os.path.join(storage_root, actual_name)

    backup_dir = os.path.join(storage_root, 'backup')
    try:
        os.makedirs(backup_dir, exist_ok=True)
        dest_name = os.path.basename(old_path)
        dest = os.path.join(backup_dir, dest_name)
        if os.path.exists(dest):
            stem, ext = os.path.splitext(dest_name)
            ts = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
            dest = os.path.join(backup_dir, f"{stem}_{ts}{ext}")
        shutil.move(old_path, dest)
        logging.info("PBO archivé : %s → %s", old_path, dest)
        return dest
    except Exception as e:
        logging.warning("Échec archivage PBO %s : %s", old_path, e)
        return None


def _compare_versions(existing_version, new_version_raw):
    """Retourne (old_str, new_str, is_newer). Compare en int quand possible (évite \"10\" < \"7\")."""
    new_stripped = str(new_version_raw).lstrip('Vv')
    try:
        existing_v = int(existing_version)
        new_v = int(new_stripped)
        return str(existing_v), str(new_v), new_v > existing_v
    except (TypeError, ValueError):
        existing_s = str(existing_version)
        return existing_s, new_stripped, new_stripped > existing_s


def build_pbo_filename_index(storage_root=None):
    """
    Index des .pbo à la racine du stockage : basename.lower() → basename réel.
    Nécessaire sous Linux (FS sensible à la casse) : DB en altis, fichier Altis.pbo.
    Ignore les fichiers Mare_aux_canards (templates / hors inventaire).
    """
    root = storage_root if storage_root is not None else settings.MISSIONS_PBO_STORAGE_PATH
    index = {}
    if not root or not os.path.isdir(root):
        return index
    try:
        for filename in os.listdir(root):
            if not filename.lower().endswith('.pbo'):
                continue
            if 'mare_aux_canards' in filename.lower():
                continue
            full = os.path.join(root, filename)
            if os.path.isfile(full):
                index[filename.lower()] = filename
    except OSError as e:
        logging.warning("Impossible de lister les PBO dans %s : %s", root, e)
    return index


def mission_pbo_candidate_names(mission):
    """Noms de fichiers candidats (map en minuscules, V/v) pour une mission."""
    version = str(mission.version).lstrip('Vv')
    name = mission.name
    map_name = (mission.map or '').lower()
    return (
        f"{name}-V{version}.{map_name}.pbo",
        f"{name}-v{version}.{map_name}.pbo",
    )


def resolve_pbo_on_disk(candidates, pbo_index=None, storage_root=None):
    """
    Retourne le nom de fichier réel sur disque, ou None.
    Comparaison insensible à la casse via l'index.
    """
    if pbo_index is None:
        pbo_index = build_pbo_filename_index(storage_root)
    for candidate in candidates:
        actual = pbo_index.get(candidate.lower())
        if actual:
            return actual
    return None


def mission_pbo_on_disk(mission, pbo_index=None):
    """True si un fichier .pbo correspondant à la mission est présent en stockage."""
    return resolve_pbo_on_disk(
        mission_pbo_candidate_names(mission),
        pbo_index=pbo_index,
    ) is not None


def pbo_filename_on_disk(filename, pbo_index=None):
    """True si ce nom de fichier .pbo est déjà présent en stockage (casse ignorée)."""
    if not filename:
        return False
    basename = os.path.basename(filename)
    if pbo_index is None:
        pbo_index = build_pbo_filename_index()
    return basename.lower() in pbo_index


def scan_missions_pbo_presence():
    """
    Parcourt toutes les missions, met à jour Mission.pbo_missing selon le disque.
    Ne modifie pas le statut jouable. Utilise update() pour éviter last_status_update.
    Étape 2 : parmi les manquants, propose une MAJ si un PBO plus récent existe sur disque.
    """
    pbo_index = build_pbo_filename_index()
    total = 0
    missing_count = 0
    ok_count = 0
    updated_count = 0
    missing_missions = []

    for mission in Mission.objects.all().iterator():
        total += 1
        missing = not mission_pbo_on_disk(mission, pbo_index=pbo_index)
        if missing != mission.pbo_missing:
            Mission.objects.filter(pk=mission.pk).update(pbo_missing=missing)
            updated_count += 1
            mission.pbo_missing = missing
        if missing:
            missing_count += 1
            missing_missions.append({
                'id': mission.id,
                'name': mission.name,
                'version': mission.version,
                'map': mission.map,
                'status': mission.status,
                'status_display': mission.get_status_display(),
            })
        else:
            ok_count += 1

    upgrade_proposals = find_newer_pbo_matches(
        [m for m in Mission.objects.filter(pbo_missing=True)],
        pbo_index=pbo_index,
    )
    upgrade_ids = {p['mission_id'] for p in upgrade_proposals}
    upgrade_filenames = {p['filename'].lower() for p in upgrade_proposals}
    still_missing = [m for m in missing_missions if m['id'] not in upgrade_ids]
    orphan_pbos = find_orphan_pbo_files(pbo_index=pbo_index, exclude_filenames=upgrade_filenames)

    return {
        'total': total,
        'missing_count': missing_count,
        'ok_count': ok_count,
        'updated_count': updated_count,
        'missing_missions': still_missing,
        'upgrade_proposals': upgrade_proposals,
        'upgrade_count': len(upgrade_proposals),
        'orphan_pbos': orphan_pbos,
        'orphan_count': len(orphan_pbos),
        'files_indexed': len(pbo_index),
        'storage_path': settings.MISSIONS_PBO_STORAGE_PATH,
    }


def find_orphan_pbo_files(pbo_index=None, exclude_filenames=None):
    """
    PBO présents sur disque sans mission exacte (même nom + carte + version).
    exclude_filenames : noms déjà listés en proposition de MAJ (évite le doublon).
    """
    if pbo_index is None:
        pbo_index = build_pbo_filename_index()
    exclude = {f.lower() for f in (exclude_filenames or [])}

    linked = set()
    name_map_missions = {}
    for mission in Mission.objects.all().only('id', 'name', 'map', 'version').iterator():
        map_l = (mission.map or '').lower()
        ver = str(mission.version).lstrip('Vv')
        linked.add((mission.name, map_l, ver))
        name_map_missions.setdefault((mission.name, map_l), []).append(mission)

    orphans = []
    for _filename_lower, filename in pbo_index.items():
        if filename.lower() in exclude:
            continue
        parsed = parse_mission_filename(filename)
        if not parsed:
            orphans.append({
                'filename': filename,
                'name': None,
                'version': None,
                'map': None,
                'parse_ok': False,
                'related_mission_id': None,
                'related_version': None,
            })
            continue
        mission_name, _mission_type, _max_players, version, map_name = parsed
        map_l = (map_name or '').lower()
        ver = str(version).lstrip('Vv')
        if (mission_name, map_l, ver) in linked:
            continue
        related_list = name_map_missions.get((mission_name, map_l)) or []
        related = related_list[0] if related_list else None
        orphans.append({
            'filename': filename,
            'name': mission_name,
            'version': ver,
            'map': map_l,
            'parse_ok': True,
            'related_mission_id': related.id if related else None,
            'related_version': str(related.version).lstrip('Vv') if related else None,
        })

    return sorted(orphans, key=lambda o: (o['filename'] or '').lower())


def find_newer_pbo_matches(missions=None, pbo_index=None):
    """
    Pour des missions sans PBO exact, cherche dans le stockage un .pbo même nom+map
    avec une version strictement supérieure. Garde la meilleure version par mission.
    """
    if missions is None:
        missions = list(Mission.objects.filter(pbo_missing=True))
    else:
        missions = list(missions)

    by_key = {}
    for mission in missions:
        key = (mission.name, (mission.map or '').lower())
        by_key.setdefault(key, []).append(mission)

    if pbo_index is None:
        pbo_index = build_pbo_filename_index()
    if not pbo_index:
        return []

    proposals = {}
    for filename_lower, filename in pbo_index.items():
        parsed = parse_mission_filename(filename)
        if not parsed:
            continue
        mission_name, mission_type, max_players, version, map_name = parsed
        key = (mission_name, (map_name or '').lower())
        candidates = by_key.get(key) or []
        for mission in candidates:
            old_v, new_v, is_newer = _compare_versions(mission.version, version)
            if not is_newer:
                continue
            prev = proposals.get(mission.id)
            if prev is not None:
                _, _, better_than_prev = _compare_versions(prev['new_version'], version)
                if not better_than_prev:
                    continue
            version_raw = version if str(version).upper().startswith('V') else f'V{new_v}'
            proposals[mission.id] = {
                'mission_id': mission.id,
                'name': mission.name,
                'map': mission.map,
                'status': mission.status,
                'status_display': mission.get_status_display(),
                'old_version': old_v,
                'new_version': new_v,
                'filename': filename,
                'mission_type': mission_type,
                'max_players': str(max_players),
                'version_raw': version_raw,
            }

    return sorted(proposals.values(), key=lambda p: (p['name'], p['map']))


def clear_mission_pbo_missing(mission):
    """Remet pbo_missing=False après écriture réussie du PBO sur disque."""
    if mission is None:
        return
    if mission.pbo_missing:
        Mission.objects.filter(pk=mission.pk).update(pbo_missing=False)
        mission.pbo_missing = False



def analyze_pbo_upload(filename, user=None, *, allow_pbo_restore=False):
    """
    Décide l'action à proposer pour un PBO sans créer/mettre à jour.
    Retourne un dict: action (create|update|create_duplicate|error), error, details.

    allow_pbo_restore : mode récupération — si la mission existe déjà (PBO souvent
    manquant après perte serveur), propose une restauration si le PBO est absent.
    Pas d'écrasement si le fichier existe déjà sur le serveur.
    """
    invalid_msg = (
        "Nom de fichier invalide. Format attendu : "
        "CPC-TypeDeMission[XX]-Nom_De_La_Mission-VY.nom_de_map.pbo (XX = 2 chiffres)"
    )
    filename_relaxed = False
    if allow_pbo_restore and RECUP_RELAX_FILENAME:
        parsed_pack = recup_parse_mission_filename(filename)
        if not parsed_pack:
            return {
                'action': 'error',
                'error': "Fichier .pbo illisible ou nom vide.",
                'details': {},
            }
        parsed, filename_relaxed = parsed_pack
    elif allow_pbo_restore:
        # Récup : un peu plus souple (tags -HC / -(HC), version _V2) sans accepter n'importe quoi
        strict = parse_mission_filename(filename)
        parsed = strict or parse_mission_filename_lenient(filename)
        if not parsed:
            return {
                'action': 'error',
                'error': (
                    "Nom de fichier invalide. Format attendu : "
                    "CPC-TypeDeMission[XX]-Nom_De_La_Mission-VY[-TAG].nom_de_map.pbo "
                    "(TAG optionnel ex. -HC ou -(HC))"
                ),
                'details': {},
            }
        filename_relaxed = strict is None
    else:
        parsed = parse_mission_filename(filename)
        if not parsed:
            return {
                'action': 'error',
                'error': invalid_msg,
                'details': {},
            }

    mission_name, mission_type, max_players, version, map_name = parsed
    map_name = map_name.lower()
    new_version = version.lstrip('Vv')
    details = {
        'mission_name': mission_name,
        'mission_type': mission_type,
        'max_players': int(max_players),
        'version': new_version,
        'version_raw': version if str(version).upper().startswith('V') else f'V{new_version}',
        'map': map_name,
        'map_display': get_map_display(map_name),
        'filename_relaxed': filename_relaxed,
    }

    # Récup : jamais écraser un PBO déjà présent sous ce nom de fichier
    if allow_pbo_restore and pbo_filename_on_disk(filename):
        return {
            'action': 'error',
            'error': (
                f"Le fichier PBO existe déjà sur le serveur, remplacement non autorisé. "
                f"(Fichier : {filename})"
            ),
            'details': details,
        }

    existing = Mission.objects.filter(
        name=mission_name, map=map_name, max_players=int(max_players)
    )
    # En récup : si pas de match exact, tenter nom+carte (max_players souvent faux hors convention)
    if allow_pbo_restore and not existing.exists():
        existing = Mission.objects.filter(name=mission_name, map=map_name)
    if existing.exists():
        existing_mission = existing.first()
        old_version, new_v, is_newer = _compare_versions(existing_mission.version, version)
        details['old_version'] = old_version
        details['existing_mission_id'] = existing_mission.id
        details['owner'] = existing_mission.user.username if existing_mission.user else '—'
        details['pbo_missing'] = not mission_pbo_on_disk(existing_mission)
        details['max_players'] = existing_mission.max_players
        if is_newer:
            details['new_version'] = new_v
            details['restore'] = False
            return {
                'action': 'update',
                'error': None,
                'details': details,
            }
        if allow_pbo_restore:
            if details['pbo_missing']:
                details['new_version'] = new_v
                details['restore'] = True
                return {
                    'action': 'update',
                    'error': None,
                    'details': details,
                }
            return {
                'action': 'error',
                'error': (
                    f"Le PBO de cette mission existe déjà sur le serveur, "
                    f"remplacement non autorisé. (Fichier : {filename})"
                ),
                'details': details,
            }
        return {
            'action': 'error',
            'error': (
                f"Une mission avec ce nom, cette carte et ce nombre de joueurs max existe déjà "
                f"avec une version supérieure ou égale ({existing_mission.version}). "
                f"(Fichier : {filename})"
            ),
            'details': details,
        }

    duplicates_qs = Mission.objects.filter(name=mission_name)
    if duplicates_qs.exists():
        duplicate_missions = []
        for m in duplicates_qs:
            duplicate_missions.append({
                'full_name': m.name,
                'map': get_map_display(m.map),
                'owner': m.user.username if m.user else '—',
            })
        details['duplicates'] = duplicate_missions
        return {
            'action': 'create_duplicate',
            'error': None,
            'details': details,
        }

    return {
        'action': 'create',
        'error': None,
        'details': details,
    }


def _mission_maker_forbidden_json():
    return JsonResponse(
        {'success': False, 'error': "Vous n'avez pas le droit de publier une mission."},
        status=403,
    )


def _recup_forbidden_json():
    return JsonResponse(
        {'success': False, 'error': "Vous n'avez pas le droit de récupérer une mission."},
        status=403,
    )


@login_required
def upload_mission(request):
    """Page d'upload multi-PBO (analyse + récap + commit via endpoints JSON)."""
    if not user_is_mission_maker(request.user):
        return HttpResponse("Vous n'avez pas le droit de publier une mission.", status=403)
    clean_temp_files(get_upload_temp_dir(request.user))
    return render(request, 'gdc_storm/upload_mission.html', {
        'max_files': UPLOAD_ANALYZE_MAX_FILES,
    })


@login_required
@require_POST
def upload_analyze(request):
    """Analyse un ou plusieurs PBO et propose une action par fichier."""
    if not user_is_mission_maker(request.user):
        return _mission_maker_forbidden_json()

    clean_temp_files(get_upload_temp_dir(request.user))
    files = list(request.FILES.getlist('pbo_files'))
    if not files:
        # Compat: un seul champ pbo_file
        single = request.FILES.get('pbo_file')
        if single:
            files = [single]
    if not files:
        return JsonResponse(
            {'success': False, 'error': "Aucun fichier .pbo fourni."},
            status=400,
        )
    if len(files) > UPLOAD_ANALYZE_MAX_FILES:
        return JsonResponse(
            {
                'success': False,
                'error': f"Trop de fichiers (max {UPLOAD_ANALYZE_MAX_FILES} par analyse).",
            },
            status=400,
        )

    items = []
    for uploaded in files:
        filename = uploaded.name
        if not filename.lower().endswith('.pbo'):
            items.append({
                'id': str(uuid.uuid4()),
                'filename': filename,
                'temp_file_path': None,
                'temp_file_name': None,
                'action': 'error',
                'error': "Le fichier doit être un .pbo.",
                'details': {},
            })
            continue
        try:
            temp_file_path, temp_file_name, filename = save_uploaded_pbo_to_temp(uploaded, request.user)
        except ValueError as e:
            items.append({
                'id': str(uuid.uuid4()),
                'filename': getattr(uploaded, 'name', '') or '',
                'temp_file_path': None,
                'temp_file_name': None,
                'action': 'error',
                'error': str(e),
                'details': {},
            })
            continue
        analysis = analyze_pbo_upload(filename, request.user)
        items.append({
            'id': str(uuid.uuid4()),
            'filename': filename,
            'temp_file_path': temp_file_path,
            'temp_file_name': temp_file_name,
            'action': analysis['action'],
            'error': analysis.get('error'),
            'details': analysis.get('details') or {},
        })

    return JsonResponse({'success': True, 'items': items})


@login_required
@require_POST
def upload_commit(request):
    """Exécute les actions confirmées pour chaque PBO analysé."""
    if not user_is_mission_maker(request.user):
        return _mission_maker_forbidden_json()

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'success': False, 'error': 'JSON invalide.'}, status=400)

    items = payload.get('items')
    if not isinstance(items, list) or not items:
        return JsonResponse(
            {'success': False, 'error': 'Aucun élément à traiter.'},
            status=400,
        )
    if len(items) > UPLOAD_ANALYZE_MAX_FILES:
        return JsonResponse(
            {
                'success': False,
                'error': f"Trop d'éléments (max {UPLOAD_ANALYZE_MAX_FILES}).",
            },
            status=400,
        )

    results = []
    for item in items:
        filename = item.get('filename') or ''
        temp_file_path = item.get('temp_file_path')
        temp_file_name = item.get('temp_file_name')
        action = item.get('action')
        confirm_publish = bool(item.get('confirm_publish'))
        confirm_update = bool(item.get('confirm_update'))

        result = {
            'filename': filename,
            'action': action,
            'success': False,
            'error': None,
            'mission_id': None,
            'mission_url': None,
        }

        if action == 'error':
            result['error'] = item.get('error') or 'Action en erreur.'
            results.append(result)
            continue

        if not is_safe_upload_temp_path(temp_file_path, request.user):
            result['error'] = "Fichier temporaire manquant, expiré ou chemin invalide."
            results.append(result)
            continue

        if not filename and temp_file_name:
            filename = temp_file_name.split('_', 1)[-1] if '_' in temp_file_name else temp_file_name
            result['filename'] = filename

        # Re-analyse au commit pour éviter les décalages / confirmations oubliées
        analysis = analyze_pbo_upload(filename, request.user)
        current_action = analysis['action']
        details = analysis.get('details') or {}

        if current_action == 'error':
            result['error'] = analysis.get('error') or 'Analyse en erreur.'
            results.append(result)
            continue

        if current_action == 'create_duplicate' and not confirm_publish:
            result['error'] = "Confirmation « publier quand même » requise (doublon de nom)."
            results.append(result)
            continue

        if current_action == 'update' and not confirm_update:
            result['error'] = "Confirmation de mise à jour requise."
            results.append(result)
            continue

        if action in ('create', 'create_duplicate') and current_action == 'update':
            result['error'] = "La mission existe déjà : une mise à jour est requise (ré-analysez)."
            results.append(result)
            continue

        mission_name = details['mission_name']
        mission_type = details['mission_type']
        max_players = details['max_players']
        version = details.get('version_raw') or f"V{details['version']}"
        map_name = details['map']

        try:
            if current_action in ('create', 'create_duplicate'):
                MapName.objects.get_or_create(code_name=map_name, defaults={'display_name': ''})
                mission, error_message = create_mission_from_pbo(
                    request, temp_file_path, filename, mission_name, mission_type,
                    max_players, version, map_name,
                )
            elif current_action == 'update':
                existing_mission = Mission.objects.filter(
                    name=mission_name, map=map_name, max_players=int(max_players)
                ).first()
                if not existing_mission:
                    result['error'] = "Mission à mettre à jour introuvable."
                    results.append(result)
                    continue
                mission, error_message = update_mission_from_pbo(
                    request, existing_mission, temp_file_path, filename,
                    mission_type, max_players, version, map_name,
                )
            else:
                result['error'] = f"Action inconnue : {current_action}"
                results.append(result)
                continue
        except ValueError as ve:
            result['error'] = str(ve)
            results.append(result)
            continue
        except Exception as e:
            logging.exception("Erreur lors du commit upload PBO")
            result['error'] = str(e)
            results.append(result)
            continue

        if error_message:
            result['error'] = error_message
            if mission:
                result['mission_id'] = mission.id
                result['mission_url'] = reverse('mission_detail', args=[mission.id])
                result['success'] = True  # mission créée mais PBO éventuellement en warning
            # Ne pas supprimer le temp si le déplacement PBO a échoué
            results.append(result)
            continue

        result['success'] = True
        result['mission_id'] = mission.id
        result['mission_url'] = reverse('mission_detail', args=[mission.id]) + '?success=1'
        # Temp déjà déplacé vers le stockage PBO ; nettoyer s'il reste (cas edge)
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except OSError:
                pass
        results.append(result)

    success_count = sum(1 for r in results if r['success'])
    fail_count = len(results) - success_count
    return JsonResponse({
        'success': fail_count == 0,
        'success_count': success_count,
        'fail_count': fail_count,
        'results': results,
    })


@login_required
def recup_missions(request):
    """Page de récupération multi-PBO depuis le cache joueur (utilisateur actif requis)."""
    if not user_can_recup(request.user):
        return HttpResponse("Vous n'avez pas le droit de récupérer une mission.", status=403)
    clean_temp_files(get_upload_temp_dir(request.user))
    return render(request, 'gdc_storm/recup_missions.html', {
        'max_files': RECUP_ANALYZE_MAX_FILES,
    })


def _require_superuser(request):
    if not request.user.is_superuser:
        return HttpResponse("Accès réservé aux administrateurs.", status=403)
    return None


@login_required
def scan_pbo_missing(request):
    """Page admin : liste des missions marquées pbo_missing + bouton pour lancer un scan."""
    forbidden = _require_superuser(request)
    if forbidden:
        return forbidden
    pbo_index = build_pbo_filename_index()
    missing_qs = Mission.objects.filter(pbo_missing=True).order_by('name', 'version')
    upgrade_proposals = find_newer_pbo_matches(missing_qs, pbo_index=pbo_index)
    upgrade_ids = {p['mission_id'] for p in upgrade_proposals}
    upgrade_filenames = {p['filename'].lower() for p in upgrade_proposals}
    missing_missions = [m for m in missing_qs if m.id not in upgrade_ids]
    orphan_pbos = find_orphan_pbo_files(pbo_index=pbo_index, exclude_filenames=upgrade_filenames)
    return render(request, 'gdc_storm/scan_pbo_missing.html', {
        'missing_missions': missing_missions,
        'missing_count': len(missing_missions),
        'upgrade_proposals': upgrade_proposals,
        'upgrade_count': len(upgrade_proposals),
        'orphan_pbos': orphan_pbos,
        'orphan_count': len(orphan_pbos),
        'files_indexed': len(pbo_index),
    })


@login_required
@require_POST
def scan_pbo_missing_run(request):
    """Lance le scan PBO pour toutes les missions et retourne un résumé JSON."""
    forbidden = _require_superuser(request)
    if forbidden:
        return JsonResponse({'success': False, 'error': 'Accès réservé aux administrateurs.'}, status=403)
    summary = scan_missions_pbo_presence()
    return JsonResponse({'success': True, **summary})


@login_required
@require_POST
def scan_pbo_missing_apply(request):
    """Applique une mise à jour de mission depuis un PBO déjà présent en stockage (version plus récente)."""
    forbidden = _require_superuser(request)
    if forbidden:
        return JsonResponse({'success': False, 'error': 'Accès réservé aux administrateurs.'}, status=403)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (TypeError, ValueError, UnicodeDecodeError):
        payload = {}
    mission_id = payload.get('mission_id') or request.POST.get('mission_id')
    filename = payload.get('filename') or request.POST.get('filename')
    if not mission_id or not filename:
        return JsonResponse({'success': False, 'error': 'Paramètres mission_id et filename requis.'}, status=400)

    filename = os.path.basename(str(filename))
    try:
        mission = Mission.objects.get(pk=int(mission_id))
    except (Mission.DoesNotExist, TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Mission introuvable.'}, status=404)

    pbo_index = build_pbo_filename_index()
    actual_filename = pbo_index.get(filename.lower())
    if not actual_filename:
        return JsonResponse({'success': False, 'error': 'Fichier PBO introuvable sur le serveur.'}, status=404)
    filename = actual_filename

    parsed = parse_mission_filename(filename)
    if not parsed:
        return JsonResponse({'success': False, 'error': 'Nom de fichier PBO invalide.'}, status=400)
    mission_name, mission_type, max_players, version, map_name = parsed
    if mission_name != mission.name or (map_name or '').lower() != (mission.map or '').lower():
        return JsonResponse(
            {'success': False, 'error': 'Le fichier PBO ne correspond pas à cette mission (nom/carte).'},
            status=400,
        )
    _, new_v, is_newer = _compare_versions(mission.version, version)
    if not is_newer:
        return JsonResponse(
            {'success': False, 'error': 'Le fichier n\'a pas une version plus récente que la mission.'},
            status=400,
        )

    pbo_path = os.path.join(settings.MISSIONS_PBO_STORAGE_PATH, filename)
    if not os.path.isfile(pbo_path):
        return JsonResponse({'success': False, 'error': 'Fichier PBO introuvable sur le serveur.'}, status=404)

    version_raw = version if str(version).upper().startswith('V') else f'V{new_v}'
    updated, error_message = update_mission_from_pbo(
        request,
        mission,
        pbo_path,
        filename,
        mission_type,
        max_players,
        version_raw,
        map_name,
        strict=False,
        preserve_owner=True,
        skip_pbo_storage=True,
    )
    if updated is None:
        return JsonResponse({'success': False, 'error': error_message or 'Échec de la mise à jour.'}, status=400)

    clear_mission_pbo_missing(updated)
    return JsonResponse({
        'success': True,
        'mission_id': updated.id,
        'name': updated.name,
        'version': updated.version,
        'map': updated.map,
        'warning': error_message,
    })


def _parse_recup_filename_for_dedupe(filename):
    """
    Parse un nom de fichier pour la dédup récup.
    Retourne (mission_name, map, max_players, version) ou None si invalide.
    """
    if RECUP_RELAX_FILENAME:
        parsed_pack = recup_parse_mission_filename(filename)
        if not parsed_pack:
            return None
        parsed, _relaxed = parsed_pack
    else:
        parsed = parse_mission_filename(filename) or parse_mission_filename_lenient(filename)
        if not parsed:
            return None
    mission_name, _mission_type, max_players, version, map_name = parsed
    return mission_name, map_name.lower(), int(max_players), version


def dedupe_recup_filenames(filenames):
    """
    Parmi une seule version (la plus récente) par mission dans le lot.
    Retourne (kept_filenames, skipped) où skipped = [{filename, reason}, ...].
    """
    skipped = []
    best_by_key = {}  # key -> (filename, version_raw)

    for raw_name in filenames:
        filename = os.path.basename(str(raw_name or '').strip())
        if not filename:
            continue
        parsed = _parse_recup_filename_for_dedupe(filename)
        if not parsed:
            skipped.append({
                'filename': filename,
                'reason': (
                    "Nom de fichier invalide. Format attendu : "
                    "CPC-TypeDeMission[XX]-Nom_De_La_Mission-VY[-TAG].nom_de_map.pbo"
                ),
            })
            continue

        mission_name, map_name, max_players, version = parsed
        key = (mission_name, map_name, max_players)
        if key not in best_by_key:
            best_by_key[key] = (filename, version)
            continue

        prev_filename, prev_version = best_by_key[key]
        _old, _new, is_newer = _compare_versions(
            str(prev_version).lstrip('Vv'),
            version,
        )
        if is_newer:
            skipped.append({
                'filename': prev_filename,
                'reason': (
                    "Version plus ancienne dans le lot — "
                    "seule la plus récente est conservée."
                ),
            })
            best_by_key[key] = (filename, version)
        else:
            skipped.append({
                'filename': filename,
                'reason': (
                    "Version plus ancienne dans le lot — "
                    "seule la plus récente est conservée."
                ),
            })

    kept = [fn for fn, _ver in best_by_key.values()]
    return kept, skipped


@login_required
@require_POST
def recup_prefetch(request):
    """
    Pré-filtre récup : analyse les noms seuls (sans upload de contenu).
    Déduplique les versions du lot, puis indique quels fichiers sont utiles.
    """
    if not user_can_recup(request.user):
        return _recup_forbidden_json()
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'success': False, 'error': 'JSON invalide.'}, status=400)

    filenames = payload.get('filenames')
    if not isinstance(filenames, list) or not filenames:
        return JsonResponse(
            {'success': False, 'error': 'Aucun nom de fichier fourni.'},
            status=400,
        )
    if len(filenames) > RECUP_ANALYZE_MAX_FILES:
        return JsonResponse(
            {
                'success': False,
                'error': f"Trop de fichiers (max {RECUP_ANALYZE_MAX_FILES}).",
            },
            status=400,
        )

    kept, skipped = dedupe_recup_filenames(filenames)
    needed = []
    for filename in kept:
        analysis = analyze_pbo_upload(filename, request.user, allow_pbo_restore=True)
        if analysis['action'] == 'error':
            skipped.append({
                'filename': filename,
                'reason': analysis.get('error') or 'Ignoré',
            })
        else:
            needed.append({
                'filename': filename,
                'action': analysis['action'],
                'details': analysis.get('details') or {},
            })

    return JsonResponse({
        'success': True,
        'needed': needed,
        'skipped': skipped,
    })


@login_required
@require_POST
def recup_analyze(request):
    """Analyse un ou plusieurs PBO pour la récupération (même logique doublons/versions que l'upload)."""
    if not user_can_recup(request.user):
        return _recup_forbidden_json()
    clean_temp_files(get_upload_temp_dir(request.user))
    files = list(request.FILES.getlist('pbo_files'))
    if not files:
        single = request.FILES.get('pbo_file')
        if single:
            files = [single]
    if not files:
        return JsonResponse(
            {'success': False, 'error': "Aucun fichier .pbo fourni."},
            status=400,
        )
    if len(files) > RECUP_ANALYZE_MAX_FILES:
        return JsonResponse(
            {
                'success': False,
                'error': f"Trop de fichiers (max {RECUP_ANALYZE_MAX_FILES} par analyse).",
            },
            status=400,
        )

    items = []
    for uploaded in files:
        filename = uploaded.name
        if not filename.lower().endswith('.pbo'):
            items.append({
                'id': str(uuid.uuid4()),
                'filename': filename,
                'temp_file_path': None,
                'temp_file_name': None,
                'action': 'error',
                'error': "Le fichier doit être un .pbo.",
                'details': {},
            })
            continue
        try:
            temp_file_path, temp_file_name, filename = save_uploaded_pbo_to_temp(uploaded, request.user)
        except ValueError as e:
            items.append({
                'id': str(uuid.uuid4()),
                'filename': getattr(uploaded, 'name', '') or '',
                'temp_file_path': None,
                'temp_file_name': None,
                'action': 'error',
                'error': str(e),
                'details': {},
            })
            continue
        analysis = analyze_pbo_upload(filename, request.user, allow_pbo_restore=True)
        items.append({
            'id': str(uuid.uuid4()),
            'filename': filename,
            'temp_file_path': temp_file_path,
            'temp_file_name': temp_file_name,
            'action': analysis['action'],
            'error': analysis.get('error'),
            'details': analysis.get('details') or {},
        })

    return JsonResponse({'success': True, 'items': items})


@login_required
@require_POST
def recup_commit(request):
    """Commit récupération : création sous GDC-RECUP, update sans changer le propriétaire, validation soft."""
    if not user_can_recup(request.user):
        return _recup_forbidden_json()
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'success': False, 'error': 'JSON invalide.'}, status=400)

    items = payload.get('items')
    if not isinstance(items, list) or not items:
        return JsonResponse(
            {'success': False, 'error': 'Aucun élément à traiter.'},
            status=400,
        )
    if len(items) > RECUP_ANALYZE_MAX_FILES:
        return JsonResponse(
            {
                'success': False,
                'error': f"Trop d'éléments (max {RECUP_ANALYZE_MAX_FILES}).",
            },
            status=400,
        )

    recup_user = get_recup_user()
    results = []
    for item in items:
        filename = item.get('filename') or ''
        temp_file_path = item.get('temp_file_path')
        temp_file_name = item.get('temp_file_name')
        action = item.get('action')
        confirm_publish = bool(item.get('confirm_publish'))
        confirm_update = bool(item.get('confirm_update'))

        result = {
            'filename': filename,
            'action': action,
            'success': False,
            'error': None,
            'mission_id': None,
            'mission_url': None,
        }

        if action == 'error':
            result['error'] = item.get('error') or 'Action en erreur.'
            results.append(result)
            continue

        if not is_safe_upload_temp_path(temp_file_path, request.user):
            result['error'] = "Fichier temporaire manquant, expiré ou chemin invalide."
            results.append(result)
            continue

        if not filename and temp_file_name:
            filename = temp_file_name.split('_', 1)[-1] if '_' in temp_file_name else temp_file_name
            result['filename'] = filename

        analysis = analyze_pbo_upload(filename, request.user, allow_pbo_restore=True)
        current_action = analysis['action']
        details = analysis.get('details') or {}

        if current_action == 'error':
            result['error'] = analysis.get('error') or 'Analyse en erreur.'
            results.append(result)
            continue

        if current_action == 'create_duplicate' and not confirm_publish:
            result['error'] = "Confirmation « publier quand même » requise (doublon de nom)."
            results.append(result)
            continue

        if current_action == 'update' and not confirm_update:
            result['error'] = "Confirmation de mise à jour requise."
            results.append(result)
            continue

        if action in ('create', 'create_duplicate') and current_action == 'update':
            result['error'] = "La mission existe déjà : une mise à jour est requise (ré-analysez)."
            results.append(result)
            continue

        mission_name = details['mission_name']
        mission_type = details['mission_type']
        max_players = details['max_players']
        version = details.get('version_raw') or f"V{details['version']}"
        map_name = details['map']

        try:
            if current_action in ('create', 'create_duplicate'):
                MapName.objects.get_or_create(code_name=map_name, defaults={'display_name': ''})
                mission, error_message = create_mission_from_pbo(
                    request,
                    temp_file_path,
                    filename,
                    mission_name,
                    mission_type,
                    max_players,
                    version,
                    map_name,
                    strict=False,
                    owner_user=recup_user,
                    status=Mission.STATUS_INCONNU,
                    publication_date=RECUP_PUBLICATION_DATE,
                )
            elif current_action == 'update':
                existing_id = details.get('existing_mission_id')
                existing_mission = None
                if existing_id:
                    existing_mission = Mission.objects.filter(id=existing_id).first()
                if not existing_mission:
                    existing_mission = Mission.objects.filter(
                        name=mission_name, map=map_name, max_players=int(max_players)
                    ).first()
                if not existing_mission:
                    existing_mission = Mission.objects.filter(
                        name=mission_name, map=map_name
                    ).first()
                if not existing_mission:
                    result['error'] = "Mission à mettre à jour introuvable."
                    results.append(result)
                    continue
                mission, error_message = update_mission_from_pbo(
                    request,
                    existing_mission,
                    temp_file_path,
                    filename,
                    mission_type,
                    max_players,
                    version,
                    map_name,
                    strict=False,
                    preserve_owner=True,
                )
            else:
                result['error'] = f"Action inconnue : {current_action}"
                results.append(result)
                continue
        except ValueError as ve:
            result['error'] = str(ve)
            results.append(result)
            continue
        except Exception as e:
            logging.exception("Erreur lors du commit récupération PBO")
            result['error'] = str(e)
            results.append(result)
            continue

        if error_message:
            result['error'] = error_message
            if mission:
                result['mission_id'] = mission.id
                result['mission_url'] = reverse('mission_detail', args=[mission.id])
                result['success'] = True
            results.append(result)
            continue

        result['success'] = True
        result['mission_id'] = mission.id
        result['mission_url'] = reverse('mission_detail', args=[mission.id]) + '?success=1'
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except OSError:
                pass
        results.append(result)

    success_count = sum(1 for r in results if r['success'])
    fail_count = len(results) - success_count
    return JsonResponse({
        'success': fail_count == 0,
        'success_count': success_count,
        'fail_count': fail_count,
        'results': results,
    })


MAP_DISPLAY_CACHE_KEY = 'map_display_names_v1'


def get_map_display_cache():
    """Cache global code_name → display_name (1h)."""
    map_display_cache = cache.get(MAP_DISPLAY_CACHE_KEY)
    if map_display_cache is None:
        all_maps = MapName.objects.all()
        map_display_cache = {m.code_name: m.display_name or m.code_name for m in all_maps}
        cache.set(MAP_DISPLAY_CACHE_KEY, map_display_cache, 3600)
    return map_display_cache


def get_map_display(map_code):
    """Retourne le display_name d'une carte à partir de son code_name, ou le code si non trouvé."""
    if not map_code:
        return map_code
    cache_map = get_map_display_cache()
    if map_code in cache_map:
        return cache_map[map_code]
    # Fallback casse / code inconnu
    lower = map_code.lower()
    for code, display in cache_map.items():
        if code.lower() == lower:
            return display
    return map_code


def get_mission_mappings(missions):
    """Retourne deux dictionnaires : id -> map_display, id -> nom complet pour une liste de missions."""
    map_displays = {}
    full_names = {}
    for mission in missions:
        map_displays[mission.id] = get_map_display(mission.map)
        full_names[mission.id] = mission.name
    return map_displays, full_names

# Mission list view
def mission_list(request):
    """Display the list of missions avec tri dynamique, y compris tri par nom de carte affiché."""
    sort = request.GET.get('sort', 'id')  # Tri par défaut sur l'id de mission
    order = request.GET.get('order', 'desc')
    sort_fields = {
        'nom': 'name',
        'proprietaire': 'user__username',
        'min': 'min_players',
        'max': 'max_players',
        'type': 'type',
        'carte': 'map',
        'date': 'publication_date',
        'jouee': 'played_count',
        'victoire': 'win_rate',
        'derniere': 'last_played_at',
        'id': 'id',
    }
    sort_field = sort_fields.get(sort, 'id')

    # Ajoute les infos "combien de fois jouée" + "dernière fois jouée" + taux de victoire
    missions_qs = Mission.objects.select_related('user').annotate(
        played_count=Count('game_sessions'),
        last_played_at=Max('game_sessions__start_time'),
        succes_count=Count(
            'game_sessions',
            filter=Q(game_sessions__verdict=GameSession.VERDICT_SUCCES),
        ),
        echec_count=Count(
            'game_sessions',
            filter=Q(game_sessions__verdict=GameSession.VERDICT_ECHEC),
        ),
    )

    if sort == 'carte':
        # Tri personnalisé sur le display_name de la carte
        missions = list(missions_qs)
        map_display = lambda m: get_map_display(m.map).lower() if get_map_display(m.map) else ''
        missions.sort(key=map_display, reverse=(order == 'desc'))
    elif sort == 'victoire':
        missions = list(missions_qs)
        for m in missions:
            decisive = m.succes_count + m.echec_count
            m.win_rate = round(100 * m.succes_count / decisive) if decisive else None
        missions.sort(
            key=lambda m: (m.win_rate is not None, m.win_rate or 0),
            reverse=(order == 'desc'),
        )
    else:
        if order == 'desc':
            sort_field = '-' + sort_field
        missions = missions_qs.order_by(sort_field)
    map_display_cache = get_map_display_cache()
    map_displays = {m.id: map_display_cache.get(m.map, m.map) for m in missions}
    full_names = {m.id: m.name for m in missions}
    # Prépare un mapping mission_id -> auteur à afficher + taux de victoire
    mission_authors_display = {}
    mission_win_rates = {}
    for m in missions:
        if m.authors and m.authors.strip() != 'Non renseigné':
            mission_authors_display[m.id] = m.authors
        elif m.user and hasattr(m.user, 'username') and m.user.username:
            mission_authors_display[m.id] = m.user.username
        else:
            mission_authors_display[m.id] = 'Non renseigné'
        decisive = m.succes_count + m.echec_count
        if decisive:
            rate = round(100 * m.succes_count / decisive)
            mission_win_rates[m.id] = f'{rate} % ({m.succes_count}/{decisive})'
            m.win_rate = rate
        else:
            mission_win_rates[m.id] = '—'
            m.win_rate = None
    return render(request, 'gdc_storm/mission_list.html', {
        'missions': missions,
        'map_displays': map_displays,
        'full_names': full_names,
        'mission_authors_display': mission_authors_display,
        'mission_win_rates': mission_win_rates,
        'sort': sort,
        'order': order
    })

# Mission detail view
def mission_detail(request, mission_id):
    mission = get_object_or_404(Mission, id=mission_id)
    success = request.GET.get('success') == '1'
    map_display = get_map_display(mission.map)
    can_edit_status = request.user.is_superuser or (mission.user == request.user)
    can_edit_owner = request.user.is_superuser
    status_form = None
    owner_form = None
    if can_edit_status:
        if request.method == 'POST' and 'update_status' in request.POST:
            status_form = MissionStatusForm(request.POST, instance=mission)
            if status_form.is_valid():
                status_form.save()
                messages.success(request, "Statut de la mission mis à jour.")
                return redirect('mission_detail', mission_id=mission.id)
        else:
            status_form = MissionStatusForm(instance=mission)
    if can_edit_owner:
        if request.method == 'POST' and 'update_owner' in request.POST:
            owner_form = MissionOwnerForm(request.POST, instance=mission)
            if owner_form.is_valid():
                owner_form.save()
                messages.success(request, "Propriétaire de la mission mis à jour.")
                return redirect('mission_detail', mission_id=mission.id)
        else:
            owner_form = MissionOwnerForm(instance=mission)
    # Sessions jouées pour cette mission
    sessions = annotate_session_player_counts(
        mission.game_sessions.all()
    ).order_by('-start_time')
    sessions_data = []
    for session in sessions:
        duration_min = None
        if session.start_time and session.end_time:
            duration_min = int((session.end_time - session.start_time).total_seconds() // 60)
        sessions_data.append({
            'id': session.id,
            'name': session.name,
            'start_time': session.start_time,
            'duration_min': duration_min,
            'verdict': session.verdict,
            'verdict_display': session.get_verdict_display(),
            'players_count': session.players_count,
            'vivant_count': session.vivant_count,
        })
    sessions_qs = mission.game_sessions.all()
    win_stats = session_win_rate(sessions_qs)
    dur_stats = duration_stats(sessions)
    avg_stats = avg_players_survivors(sessions_data)
    gsp_qs = GameSessionPlayer.objects.filter(session__mission=mission)
    roles = role_breakdown(gsp_qs, limit=5)
    top_players = top_players_for_gsp(gsp_qs, limit=5)
    arma3map_config = None  # compat tests / anciens templates
    mission_map_config = get_mission_map_config(mission.map)
    if mission_map_config and mission_map_config.get('provider') == 'arma3map':
        arma3map_config = mission_map_config
    markers_status = get_mission_markers_status(mission)
    markers_visible = markers_status['visible']
    map_supported = bool(mission_map_config)
    show_map_tab = map_supported and bool(markers_visible)
    ocap_sync_pending = (
        bool(mission_map_config)
        and mission_map_config.get('provider') == 'ocap'
        and not mission_map_config.get('local_ready')
    )
    if ocap_sync_pending and mission_map_config.get('world_name'):
        kick = maybe_kick_ocap_sync(mission_map_config['world_name'])
        if kick and kick.get('status') == 'error':
            logging.warning(
                'Sync OCAP non démarrée pour %s : %s',
                mission_map_config['world_name'],
                kick.get('error'),
            )
    briefing_marker_targets = {}
    for m in markers_status.get('raw') or []:
        if not isinstance(m, dict):
            continue
        mname = (m.get('name') or '').strip()
        if not mname:
            continue
        try:
            briefing_marker_targets[mname.lower()] = {
                'name': mname,
                'x': float(m['x']),
                'z': float(m['z']),
                'text': (m.get('text') or '').strip(),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return render(request, 'gdc_storm/mission_detail.html', {
        'mission': mission,
        'success': success,
        'map_display': map_display,
        'markers': markers_visible,
        'markers_status': markers_status,
        'mission_map_config': mission_map_config,
        'arma3map_config': arma3map_config or mission_map_config,
        'arma_marker_colors': ARMA_MARKER_COLORS,
        'arma_marker_icons': MARKER_ICON_MAP,
        'map_supported': map_supported,
        'show_map_tab': show_map_tab,
        'ocap_sync_pending': ocap_sync_pending,
        'briefing_marker_targets': briefing_marker_targets,
        'can_edit_status': can_edit_status,
        'can_edit_owner': can_edit_owner,
        'status_form': status_form,
        'owner_form': owner_form,
        'sessions_data': sessions_data,
        'win_rate_display': win_stats['win_rate_display'],
        'detail_stats': {
            **win_stats,
            **dur_stats,
            **avg_stats,
            **roles,
            'top_players': top_players,
        },
    })


@require_http_methods(['GET', 'HEAD'])
def serve_ocap_map_tile(request, world, rest):
    """Sert une tuile OCAP : stockage local Storm, sinon proxy maps-cdn."""
    from django.http import FileResponse, Http404, HttpResponse

    target = resolve_safe_tile_path(world, rest)
    if target is not None:
        content_type = 'image/png'
        lower = target.name.lower()
        if lower.endswith('.json'):
            content_type = 'application/json'
        elif lower.endswith('.jpg') or lower.endswith('.jpeg'):
            content_type = 'image/jpeg'
        if request.method == 'HEAD':
            resp = HttpResponse(status=200, content_type=content_type)
        else:
            resp = FileResponse(target.open('rb'), content_type=content_type)
        resp['Cache-Control'] = 'public, max-age=604800'
        return resp

    proxied = fetch_maps_cdn_tile(world, rest)
    if proxied is None:
        raise Http404('Tuile OCAP introuvable')
    content, content_type = proxied
    if request.method == 'HEAD':
        resp = HttpResponse(status=200, content_type=content_type)
    else:
        resp = HttpResponse(content, content_type=content_type)
    resp['Cache-Control'] = 'public, max-age=604800'
    return resp


@require_http_methods(['GET', 'POST'])
def ocap_map_sync(request):
    """Statut / sync OCAP : délègue au maps-cdn si configuré, sinon storage local."""
    if request.method == 'POST':
        world = (request.POST.get('world') or '').strip()
        if not world and request.content_type and 'json' in request.content_type:
            try:
                body = json.loads(request.body.decode('utf-8') or '{}')
            except Exception:
                body = {}
            world = (body.get('world') or '').strip()
        force = False
        if request.POST.get('force') in ('1', 'true', 'True'):
            force = True
        elif request.content_type and 'json' in (request.content_type or ''):
            try:
                body = json.loads(request.body.decode('utf-8') or '{}')
                force = bool(body.get('force'))
            except Exception:
                pass
        if not world:
            return JsonResponse({'status': 'error', 'error': 'world requis'}, status=400)
        result = start_sync_ocap_world_async(world, force=force)
        result['world'] = world
        # Toujours same-origin pour le navigateur (jamais d'IP privée CDN).
        result['tile_url_local'] = local_tile_url_template(world)
        if 'local_ready' not in result:
            if ocap_maps_cdn_url():
                result['local_ready'] = result.get('status') == 'ready'
            else:
                result['local_ready'] = is_local_map_ready(world)
        if 'syncing' not in result:
            result['syncing'] = (
                result.get('status') == 'queued'
                if ocap_maps_cdn_url()
                else is_syncing(world)
            )
        return JsonResponse(result)

    world = (request.GET.get('world') or '').strip()
    if not world:
        return JsonResponse({'status': 'error', 'error': 'world requis'}, status=400)
    return JsonResponse(ocap_sync_status(world))


# Delete mission view
@require_POST
@login_required
def delete_mission(request, mission_id):
    mission = get_object_or_404(Mission, id=mission_id)
    # Interdire la suppression si la mission est liée à au moins une GameSession
    if mission.game_sessions.exists():
        messages.error(request, "Suppression impossible : cette mission a déjà été jouée et est liée à au moins une session. Vous pouvez mettre son statut à 'supprimée'.")
        return redirect('mission_detail', mission_id=mission.id)
    # Seul l'admin ou le mission maker propriétaire peut supprimer
    if not (request.user.is_superuser or (request.user.groups.filter(name='Mission Maker').exists() and mission.user == request.user)):
        return HttpResponse("Suppression non autorisée.", status=403)
    mission_name = mission.name
    # Suppression de l'image loadScreen associée si présente
    if mission.loadScreen:
        try:
            mission.loadScreen.delete(save=False)
        except Exception:
            pass  # Ignore toute erreur de suppression d'image
    # Suppression des images de briefing associées si présentes
    if hasattr(mission, 'briefing_images') and mission.briefing_images:
        for img_path in mission.briefing_images:
            try:
                default_storage.delete(img_path)
            except Exception:
                pass
    delete_markers_file(mission.markers_file)
    # Suppression du fichier .pbo en stockage
    pbo_name = resolve_pbo_on_disk(mission_pbo_candidate_names(mission))
    if pbo_name:
        pbo_path = os.path.join(settings.MISSIONS_PBO_STORAGE_PATH, pbo_name)
        try:
            if os.path.isfile(pbo_path):
                os.remove(pbo_path)
        except Exception as e:
            logging.warning("Échec suppression PBO %s : %s", pbo_path, e)
    mission.delete()
    messages.success(request, f"Mission supprimée avec succès : {mission_name}")
    return redirect('mission_list')

def get_user_role(user):
    if user.is_superuser:
        return "Admin"
    elif user.groups.filter(name="Mission Maker").exists():
        return "Mission Maker"
    return "Utilisateur"

def user_profile(request, user_id):
    user_profile = get_object_or_404(User, id=user_id)
    # Tri et filtres missions publiées
    sort_pub = request.GET.get('sort_pub', 'date')
    order_pub = request.GET.get('order_pub', 'desc')
    filter_pub_nom = request.GET.get('filter_pub_nom', '').lower()
    filter_pub_carte = request.GET.get('filter_pub_carte', '').lower()
    missions = Mission.objects.filter(user=user_profile).order_by('-publication_date')
    map_displays, full_names = get_mission_mappings(missions)
    # Filtres missions publiées
    if filter_pub_nom:
        missions = [m for m in missions if filter_pub_nom in (m.name or '').lower()]
    if filter_pub_carte:
        missions = [m for m in missions if filter_pub_carte in (map_displays.get(m.id, '').lower())]
    # Tri missions publiées
    reverse_pub = (order_pub == 'desc')
    if sort_pub == 'nom':
        missions = sorted(missions, key=lambda m: (m.name or '').lower(), reverse=reverse_pub)
    elif sort_pub == 'carte':
        missions = sorted(missions, key=lambda m: (map_displays.get(m.id, '') or '').lower(), reverse=reverse_pub)
    elif sort_pub == 'date':
        missions = sorted(missions, key=lambda m: m.publication_date or '', reverse=reverse_pub)
    elif sort_pub == 'statut':
        missions = sorted(missions, key=lambda m: (m.status or '').lower(), reverse=reverse_pub)
    # --- Missions jouées ---
    players_qs = user_profile.players.order_by('created_at')
    oldest_player = players_qs.first()
    oldest_player_created = oldest_player.created_at if oldest_player else None
    player_ids = list(players_qs.values_list('id', flat=True))
    gamesession_players = GameSessionPlayer.objects.filter(player_id__in=player_ids).select_related('session').order_by('-session__start_time')
    sessions_played = [gsp.session for gsp in gamesession_players]
    total_sessions_played = len(set(sessions_played))
    total_missions_published = len(missions)
    session_ids = list(set([s.id for s in sessions_played]))
    sessions = annotate_session_player_counts(
        GameSession.objects.filter(id__in=session_ids).select_related('mission')
    )
    all_map_displays = {}
    for s in sessions:
        if s.mission:
            all_map_displays[s.mission.id] = get_map_display(s.mission.map)
        else:
            all_map_displays[s.id] = get_map_display(s.map)
    # Filtres sessions jouées
    sort = request.GET.get('sort', 'date')
    order = request.GET.get('order', 'desc')
    filter_nom = request.GET.get('filter_nom', '').lower()
    filter_carte = request.GET.get('filter_carte', '').lower()
    filter_verdict = request.GET.get('filter_verdict', '').lower()
    sessions_data = []
    for s in sessions:
        duration_min = None
        if s.start_time and s.end_time:
            duration_min = int((s.end_time - s.start_time).total_seconds() // 60)
        sessions_data.append({
            'session': s,
            'players_count': s.players_count,
            'vivant_count': s.vivant_count,
            'duration_min': duration_min,
        })
    # Filtres sessions
    if filter_nom:
        sessions_data = [d for d in sessions_data if filter_nom in (d['session'].name or '').lower()]
    if filter_carte:
        sessions_data = [d for d in sessions_data if filter_carte in (get_map_display(d['session'].map) or '').lower()]
    if filter_verdict:
        sessions_data = [
            d for d in sessions_data
            if filter_verdict in (d['session'].get_verdict_display() or '').lower()
        ]
    # Tri sessions
    reverse_order = (order == 'desc')
    if sort == 'nom':
        sessions_data.sort(key=lambda x: (x['session'].name or '').lower(), reverse=reverse_order)
    elif sort == 'carte':
        sessions_data.sort(key=lambda x: (get_map_display(x['session'].map) or '').lower(), reverse=reverse_order)
    elif sort == 'date':
        sessions_data.sort(key=lambda x: x['session'].start_time or '', reverse=reverse_order)
    elif sort == 'duration':
        sessions_data.sort(key=lambda x: x['duration_min'] if x['duration_min'] is not None else -1, reverse=reverse_order)
    elif sort == 'verdict':
        sessions_data.sort(key=lambda x: (x['session'].verdict or '').lower(), reverse=reverse_order)
    user_status_by_session_id = {}
    for gsp in gamesession_players:
        user_status_by_session_id[gsp.session_id] = gsp.status

    survival = player_survival_stats(user_status_by_session_id)
    dead_alive_ratio = {
        'alive': survival['alive'],
        'dead': survival['dead'],
        'ratio': survival['ratio_display'],
        'survival_pct': survival['survival_pct'],
    }
    sessions_qs = GameSession.objects.filter(id__in=session_ids)
    win_stats = session_win_rate(sessions_qs)
    dur_stats = duration_stats(sessions)
    roles = role_breakdown(gamesession_players, limit=5)
    top_maps = top_maps_for_sessions(sessions_qs, get_map_display, limit=5)
    published_wr = published_missions_win_rate(
        Mission.objects.filter(user=user_profile)
    )

    context = {
        'user_profile': user_profile,
        'user_role': get_user_role(user_profile),
        'missions': missions,
        'map_displays': map_displays,
        'full_names': full_names,
        'sessions_played': sessions_played,
        'oldest_player_created': oldest_player_created,
        'total_sessions_played': total_sessions_played,
        'dead_alive_ratio': dead_alive_ratio,
        'total_missions_published': total_missions_published,
        'sessions_data': sessions_data,
        'user_sessions_map_displays': all_map_displays,
        'user_status_by_session_id': user_status_by_session_id,
        'sort': sort,
        'order': order,
        'sort_pub': sort_pub,
        'order_pub': order_pub,
        'filter_pub_nom': filter_pub_nom,
        'filter_pub_carte': filter_pub_carte,
        'filter_nom': filter_nom,
        'filter_carte': filter_carte,
        'filter_verdict': filter_verdict,
        'detail_stats': {
            **win_stats,
            **dur_stats,
            **roles,
            'top_maps': top_maps,
            'survival_pct': survival['survival_pct'],
            'published_win_rate_display': published_wr['win_rate_display'],
            'published_sessions_count': published_wr['sessions_count'],
        },
    }
    return render(request, 'gdc_storm/user_profile.html', context)

def create_mission_from_pbo(
    request,
    temp_file_path,
    filename,
    mission_name,
    mission_type,
    max_players,
    version,
    map_name,
    error_message=None,
    *,
    strict=True,
    owner_user=None,
    status=None,
    publication_date=None,
):
    errors = []
    warnings = []
    try:
        pbo = PBOFile.read_file(temp_file_path)
    except Exception as e:
        errors.append(f"Erreur lors de la lecture du fichier .pbo : {e}")
        return None, format_errors(errors)
    is_binarized = is_sqm_binarized(pbo)
    if is_binarized:
        msg = (
            "Le fichier mission.sqm est binarisé. Merci de sauvegarder la mission "
            "en mode texte dans l'éditeur avant de l'uploader."
        )
        (errors if strict else warnings).append(msg)
    # --- Contrôle Headless Client ---
    try:
        sqm_file = pbo['mission.sqm']
        sqm_content = sqm_file.data.decode('utf-8', errors='replace')
        hc_regex = r'name\s*=\s*"HC_Slot";\s*isPlayable\s*=\s*1;[^}]*type\s*=\s*"HeadlessClient_F";'
        if not re.search(hc_regex, sqm_content, re.DOTALL):
            msg = (
                "Erreur : la mission ne contient pas de slot Headless Client correctement configuré. "
                "Il doit exister un slot avec name=\"HC_Slot\"; isPlayable=1; type=\"HeadlessClient_F\" "
                "dans mission.sqm."
            )
            (errors if strict else warnings).append(msg)
    except KeyError:
        msg = "Erreur : mission.sqm introuvable dans le pbo."
        (errors if strict else warnings).append(msg)
    except Exception as e:
        msg = f"Erreur lors du contrôle Headless Client : {e}"
        (errors if strict else warnings).append(msg)
    data, extraction_problems = extract_mission_data_from_pbo(pbo)
    if extraction_problems:
        msg = (
            "Problèmes détectés lors de l'extraction des métadonnées :<ul>"
            + ''.join(f"<li>{prob}</li>" for prob in extraction_problems)
            + "</ul>"
        )
        (errors if strict else warnings).append(msg)
    # Extraction du briefing et des images de briefing
    briefing = None
    briefing_images = []
    try:
        briefing, briefing_images = extract_briefing_from_pbo(pbo)
        if briefing is None:
            msg = "Erreur lors de l'extraction du briefing : briefing non trouvé ou invalide."
            if strict:
                errors.append(msg)
            else:
                warnings.append(msg)
                briefing = []
    except Exception as e:
        msg = f"Erreur lors de l'extraction du briefing : {e}"
        if strict:
            errors.append(msg)
        else:
            warnings.append(msg)
            briefing = []
    if errors:
        return None, format_errors(errors)
    loadscreen_file = None
    if data.get('loadScreen'):
        try:
            ext = os.path.splitext(data['loadScreen'])[1].lower()
            if ext in ['.jpg', '.jpeg', '.png']:
                img_entry = pbo[data['loadScreen']]
                img_data = img_entry.data
                img_filename = os.path.join(settings.MISSIONS_IMAGES_STORAGE_PATH, f"{uuid.uuid4()}{ext}")
                os.makedirs(os.path.join(default_storage.location, settings.MISSIONS_IMAGES_STORAGE_PATH), exist_ok=True)
                with default_storage.open(img_filename, 'wb') as imgfile:
                    imgfile.write(img_data)
                loadscreen_file = img_filename
            else:
                logging.info(f"Image loadScreen ignorée (format non supporté) : {data['loadScreen']}")
        except Exception as e:
            logging.warning(f"Erreur lors de l'extraction de l'image loadScreen : {e}")
            loadscreen_file = None
    owner = owner_user if owner_user is not None else request.user
    mission = Mission(
        name=mission_name,
        user=owner,
        authors=data.get('author') or '',
        min_players=int(data['minPlayers']) if data.get('minPlayers') else None,
        max_players=int(max_players),
        type=mission_type.upper(),
        version=version.lstrip('Vv'),
        map=map_name.lower(),
        onLoadMission=data.get('onLoadMission') or Mission.DEFAULT_NOT_PROVIDED,
        overviewText=data.get('overviewText') or Mission.DEFAULT_NOT_PROVIDED,
        loadScreen=loadscreen_file,
        briefing=briefing if briefing is not None else [],
    )
    if status is not None:
        mission.status = status
    # Mode récup : autorise temporairement les noms hors convention CPC
    try:
        mission.save(skip_name_check=not strict)
    except IntegrityError:
        return None, (
            "Une mission avec le même nom, carte et nombre de joueurs existe déjà "
            "(création concurrente ou doublon)."
        )
    # auto_now_add ignore toute valeur à la création : forcer via update()
    if publication_date is not None:
        pub = publication_date
        if timezone.is_naive(pub):
            pub = timezone.make_aware(pub, timezone.get_current_timezone())
        Mission.objects.filter(pk=mission.pk).update(publication_date=pub)
        mission.publication_date = pub
    # Stocke la liste des images de briefing pour suppression ultérieure
    if briefing_images:
        mission.briefing_images = briefing_images
        mission.save(update_fields=['briefing_images'], skip_name_check=not strict)
    _apply_markers_from_pbo(pbo, mission, warnings=warnings, strict=strict)
    try:
        save_pbo_to_storage(temp_file_path, filename)
    except Exception as e:
        return mission, f"Mission créée, mais erreur lors de la sauvegarde du PBO: {e}"
    clear_mission_pbo_missing(mission)
    warning_msg = format_errors(warnings)
    return mission, warning_msg


def _apply_markers_from_pbo(pbo, mission, *, warnings, strict=True):
    markers, marker_problems = extract_markers_from_pbo(pbo)
    if marker_problems:
        warnings.extend(marker_problems)
    delete_markers_file(mission.markers_file)
    markers_path = save_markers_to_storage(markers, mission_id=mission.pk)
    mission.markers_file = markers_path or ''
    mission.save(update_fields=['markers_file'], skip_name_check=not strict)


def _extract_loadscreen_from_pbo(pbo, data):
    """Extrait loadScreen du PBO vers le storage. Retourne le chemin relatif ou None."""
    loadscreen_path = data.get('loadScreen') if data else None
    if not loadscreen_path:
        return None
    try:
        ext = os.path.splitext(loadscreen_path)[1].lower()
        if ext not in ['.jpg', '.jpeg', '.png']:
            logging.info(f"Image loadScreen ignorée (format non supporté) : {loadscreen_path}")
            return None
        img_entry = pbo[loadscreen_path]
        img_filename = os.path.join(settings.MISSIONS_IMAGES_STORAGE_PATH, f"{uuid.uuid4()}{ext}")
        os.makedirs(
            os.path.join(default_storage.location, settings.MISSIONS_IMAGES_STORAGE_PATH),
            exist_ok=True,
        )
        with default_storage.open(img_filename, 'wb') as imgfile:
            imgfile.write(img_entry.data)
        return img_filename
    except Exception as e:
        logging.warning(f"Erreur lors de l'extraction de l'image loadScreen : {e}")
        return None


def reextract_mission_content_from_pbo(
    mission,
    pbo,
    *,
    briefing=True,
    markers=True,
    loadscreen=True,
    meta=False,
    dry_run=False,
):
    """
    Régénère briefing / images / loadScreen / marqueurs / métadonnées texte
    depuis un PBO déjà stocké. Ne modifie pas name, version, map, type, max_players.
    Retourne (summary_dict, notes_list).
    """
    notes = []
    summary = {
        'briefing_items': None,
        'briefing_images': None,
        'markers': None,
        'loadscreen': None,
        'meta': False,
    }

    need_data = loadscreen or meta
    data = {}
    if need_data:
        data, extraction_problems = extract_mission_data_from_pbo(pbo)
        if extraction_problems:
            notes.extend(extraction_problems)

    if dry_run:
        if briefing:
            summary['briefing_items'] = 'would-refresh'
            summary['briefing_images'] = 'would-refresh'
        if loadscreen:
            summary['loadscreen'] = 'would-refresh' if data.get('loadScreen') else 'none'
        if markers:
            markers_preview, marker_problems = extract_markers_from_pbo(pbo)
            summary['markers'] = len(markers_preview)
            notes.extend(marker_problems)
        if meta:
            summary['meta'] = True
        return summary, notes

    update_fields = []

    if briefing:
        try:
            new_briefing, new_images = extract_briefing_from_pbo(pbo)
            if new_briefing is None:
                new_briefing = []
                notes.append('Briefing non trouvé ou invalide.')
        except Exception as e:
            new_briefing = []
            new_images = []
            notes.append(f'Erreur extraction briefing : {e}')
        old_images = list(mission.briefing_images or [])
        mission.briefing = new_briefing
        mission.briefing_images = new_images or []
        update_fields.extend(['briefing', 'briefing_images'])
        for img_path in old_images:
            try:
                default_storage.delete(img_path)
            except Exception:
                pass
        summary['briefing_items'] = len(new_briefing)
        summary['briefing_images'] = len(new_images or [])

    if loadscreen:
        if mission.loadScreen:
            try:
                mission.loadScreen.delete(save=False)
            except Exception:
                pass
        loadscreen_file = _extract_loadscreen_from_pbo(pbo, data)
        mission.loadScreen = loadscreen_file
        update_fields.append('loadScreen')
        summary['loadscreen'] = loadscreen_file or ''

    if meta:
        mission.authors = data.get('author') or ''
        mission.min_players = int(data['minPlayers']) if data.get('minPlayers') else None
        mission.onLoadMission = data.get('onLoadMission') or Mission.DEFAULT_NOT_PROVIDED
        mission.overviewText = data.get('overviewText') or Mission.DEFAULT_NOT_PROVIDED
        update_fields.extend(['authors', 'min_players', 'onLoadMission', 'overviewText'])
        summary['meta'] = True

    if update_fields:
        mission.save(update_fields=update_fields, skip_name_check=True)

    if markers:
        marker_notes = []
        _apply_markers_from_pbo(pbo, mission, warnings=marker_notes, strict=False)
        notes.extend(marker_notes)
        markers_list = load_markers_from_mission(mission) if mission.markers_file else []
        summary['markers'] = len(markers_list)

    return summary, notes


def format_errors(errors):
    if not errors:
        return None
    return '<br/>'.join(errors)


def update_mission_from_pbo(
    request,
    existing_mission,
    temp_file_path,
    filename,
    mission_type,
    max_players,
    version,
    map_name,
    *,
    strict=True,
    preserve_owner=False,
    skip_pbo_storage=False,
):
    is_admin = request.user.is_superuser
    is_owner = existing_mission.user == request.user
    # preserve_owner : récup / scan — tout utilisateur actif peut restaurer sans changer le propriétaire.
    allowed = is_admin or is_owner or (preserve_owner and user_can_recup(request.user))
    if not allowed:
        return None, "Vous n'avez pas le droit de mettre à jour cette mission (seul le propriétaire, un utilisateur actif en récupération ou un admin peut le faire)."
    if not os.path.exists(temp_file_path):
        return None, "Fichier temporaire manquant ou expiré lors de la confirmation de mise à jour. Merci de recommencer l'upload."
    warnings = []
    try:
        pbo = PBOFile.read_file(temp_file_path)
    except Exception as e:
        return None, f"Erreur lors de la lecture du fichier .pbo : {e}"
    is_binarized = is_sqm_binarized(pbo)
    if is_binarized:
        msg = (
            "Le fichier mission.sqm est binarisé. Merci de sauvegarder la mission "
            "en mode texte dans l'éditeur avant de l'uploader."
        )
        if strict:
            return None, msg
        warnings.append(msg)
    data, extraction_problems = extract_mission_data_from_pbo(pbo)
    if extraction_problems:
        error_message = "Problèmes détectés lors de l'extraction des métadonnées :"
        error_message += "<ul>"
        for prob in extraction_problems:
            error_message += f"<li>{prob}</li>"
        error_message += "</ul>"
        if strict:
            return None, error_message
        warnings.append(error_message)
    # Extraction du briefing et des images de briefing
    briefing = None
    briefing_images = []
    try:
        briefing, briefing_images = extract_briefing_from_pbo(pbo)
        if briefing is None and not strict:
            warnings.append("Erreur lors de l'extraction du briefing : briefing non trouvé ou invalide.")
            briefing = []
    except Exception as e:
        msg = f"Erreur lors de l'extraction du briefing : {e}"
        if strict:
            return None, msg
        warnings.append(msg)
        briefing = []

    # Archiver l'ancien PBO avant de changer version/map (non bloquant)
    backup_existing_pbo(existing_mission)

    if not preserve_owner and not is_admin:
        existing_mission.user = request.user
    existing_mission.authors = data.get('author') or ''
    existing_mission.min_players = int(data['minPlayers']) if data.get('minPlayers') else None
    existing_mission.max_players = int(max_players)
    existing_mission.type = mission_type.upper()
    existing_mission.version = version.lstrip('Vv')
    existing_mission.map = map_name.lower()
    existing_mission.onLoadMission = data.get('onLoadMission') or Mission.DEFAULT_NOT_PROVIDED
    existing_mission.overviewText = data.get('overviewText') or Mission.DEFAULT_NOT_PROVIDED
    existing_mission.briefing = briefing if briefing is not None else []
    # Si des images de briefing existent déjà, les supprimer
    if hasattr(existing_mission, 'briefing_images') and existing_mission.briefing_images:
        for img_path in existing_mission.briefing_images:
            try:
                default_storage.delete(img_path)
            except Exception:
                pass
    # Stocke la nouvelle liste des images de briefing
    if briefing_images:
        existing_mission.briefing_images = briefing_images
    # Si un loadScreen existe déjà, le supprimer
    if existing_mission.loadScreen:
        try:
            existing_mission.loadScreen.delete(save=False)
        except Exception:
            pass
    loadscreen_file = None
    if data.get('loadScreen'):
        try:
            ext = os.path.splitext(data['loadScreen'])[1].lower()
            if ext in ['.jpg', '.jpeg', '.png']:
                img_entry = pbo[data['loadScreen']]
                img_data = img_entry.data
                img_filename = os.path.join(settings.MISSIONS_IMAGES_STORAGE_PATH, f"{uuid.uuid4()}{ext}")
                os.makedirs(os.path.join(default_storage.location, settings.MISSIONS_IMAGES_STORAGE_PATH), exist_ok=True)
                with default_storage.open(img_filename, 'wb') as imgfile:
                    imgfile.write(img_data)
                loadscreen_file = img_filename
            else:
                logging.info(f"Image loadScreen ignorée (format non supporté) : {data['loadScreen']}")
        except Exception as e:
            logging.warning(f"Erreur lors de l'extraction de l'image loadScreen : {e}")
            loadscreen_file = None
    existing_mission.loadScreen = loadscreen_file
    existing_mission.save(skip_name_check=not strict)
    _apply_markers_from_pbo(pbo, existing_mission, warnings=warnings, strict=strict)
    if not skip_pbo_storage:
        try:
            save_pbo_to_storage(temp_file_path, filename)
        except Exception as e:
            return existing_mission, f"Mission mise à jour, mais erreur lors de la sauvegarde du PBO: {e}"
    clear_mission_pbo_missing(existing_mission)
    warning_msg = format_errors(warnings)
    return existing_mission, warning_msg

def player_list(request):
    sort = request.GET.get('sort', 'created_at')
    order = request.GET.get('order', 'asc')
    sort_fields = {
        'nom': 'name',
        'date': 'created_at',
        'last': 'last_session',
        'missions': 'missions_count',
    }
    from django.db.models import Count, Max, Subquery, OuterRef, DateTimeField, IntegerField
    gsp = GameSessionPlayer.objects.values('player').annotate(
        count=Count('session', distinct=True),
        last=Max('session__start_time')
    )
    player_sessions_count = {item['player']: item['count'] for item in gsp}
    player_last_session = {item['player']: item['last'] for item in gsp}

    players = Player.objects.all()
    last_session_subq = GameSessionPlayer.objects.filter(player=OuterRef('pk')).order_by('-session__start_time').values('session__start_time')[:1]
    missions_count_subq = GameSessionPlayer.objects.filter(player=OuterRef('pk')).values('player').annotate(cnt=Count('session', distinct=True)).values('cnt')[:1]
    players = players.annotate(
        last_session=Subquery(last_session_subq, output_field=DateTimeField()),
        missions_count=Subquery(missions_count_subq, output_field=IntegerField())
    )
    sort_field = sort_fields.get(sort, 'created_at')
    if order == 'desc':
        sort_field = '-' + sort_field
    players = players.order_by(sort_field, 'id')

    return render(request, 'gdc_storm/player_list.html', {
        'players': players,
        'sort': sort,
        'order': order,
        'player_sessions_count': player_sessions_count,
        'player_last_session': player_last_session,
    })

def player_detail(request, player_id):
    player = get_object_or_404(Player, id=player_id)
    # Si le player est lié à un utilisateur, rediriger vers la page utilisateur
    user = player.users.first()
    if user:
        return redirect(reverse('user_profile', args=[user.id]))
    # Tri dynamique
    sort = request.GET.get('sort', 'date')
    order = request.GET.get('order', 'desc')
    gamesession_players = GameSessionPlayer.objects.filter(player=player).select_related('session').order_by('-session__start_time')
    sessions_played = [gsp.session for gsp in gamesession_players]
    total_sessions_played = len(set(sessions_played))
    session_ids = list(set([s.id for s in sessions_played]))
    sessions = annotate_session_player_counts(
        GameSession.objects.filter(id__in=session_ids).select_related('mission')
    )
    # get_map_display est importé en début de fichier
    all_map_displays = {}
    for s in sessions:
        if s.mission:
            all_map_displays[s.mission.id] = get_map_display(s.mission.map)
        else:
            all_map_displays[s.id] = get_map_display(s.map)
    sessions_data = []
    for s in sessions:
        duration_min = None
        if s.start_time and s.end_time:
            duration_min = int((s.end_time - s.start_time).total_seconds() // 60)
        sessions_data.append({
            'session': s,
            'players_count': s.players_count,
            'vivant_count': s.vivant_count,
            'duration_min': duration_min,
        })
    # Tri côté Python
    reverse_order = (order == 'desc')
    if sort == 'nom':
        sessions_data.sort(key=lambda x: (x['session'].name or '').lower(), reverse=reverse_order)
    elif sort == 'carte':
        sessions_data.sort(key=lambda x: (get_map_display(x['session'].map) or '').lower(), reverse=reverse_order)
    elif sort == 'date':
        sessions_data.sort(key=lambda x: x['session'].start_time or '', reverse=reverse_order)
    elif sort == 'duration':
        sessions_data.sort(key=lambda x: x['duration_min'] if x['duration_min'] is not None else -1, reverse=reverse_order)
    elif sort == 'verdict':
        sessions_data.sort(key=lambda x: (x['session'].verdict or '').lower(), reverse=reverse_order)
    status_by_session_id = {gsp.session_id: gsp.status for gsp in gamesession_players}
    survival = player_survival_stats(status_by_session_id)
    dead_alive_ratio = {
        'alive': survival['alive'],
        'dead': survival['dead'],
        'ratio': survival['ratio_display'],
        'survival_pct': survival['survival_pct'],
    }
    sessions_qs = GameSession.objects.filter(id__in=session_ids)
    win_stats = session_win_rate(sessions_qs)
    dur_stats = duration_stats(sessions)
    roles = role_breakdown(gamesession_players, limit=5)
    top_maps = top_maps_for_sessions(sessions_qs, get_map_display, limit=5)
    return render(request, 'gdc_storm/player_detail.html', {
        'player': player,
        'sessions_played': sessions_played,
        'dead_alive_ratio': dead_alive_ratio,
        'total_sessions_played': total_sessions_played,
        'sessions_data': sessions_data,
        'player_sessions_map_displays': all_map_displays,
        'status_by_session_id': status_by_session_id,
        'sort': sort,
        'order': order,
        'detail_stats': {
            **win_stats,
            **dur_stats,
            **roles,
            'top_maps': top_maps,
            'survival_pct': survival['survival_pct'],
        },
    })

@login_required
def player_mapping(request):
    from .models import Player
    from .player_linking import linkable_player_ids

    user = request.user
    players = (
        Player.objects.annotate(users_count=Count('users'))
        .prefetch_related('users')
        .order_by('name')
    )
    linkable_ids = linkable_player_ids(user, players)
    if request.method == 'POST':
        selected_ids = request.POST.getlist('players')
        allowed_ids = {str(pid) for pid in linkable_ids}
        filtered_ids = [int(pid) for pid in selected_ids if pid in allowed_ids]
        user.players.set(filtered_ids)
        user.save()
        return render(request, 'gdc_storm/player_mapping.html', {
            'players': players,
            'success': True,
            'selected_ids': filtered_ids,
            'linkable_ids': linkable_ids,
        })
    selected_ids = list(user.players.values_list('id', flat=True))
    return render(request, 'gdc_storm/player_mapping.html', {
        'players': players,
        'selected_ids': selected_ids,
        'linkable_ids': linkable_ids,
    })

def session_list(request):
    # defaultdict est importé en début de fichier
    cache_data = cache.get(SESSION_LIST_CACHE_KEY)
    sort = request.GET.get('sort', 'date')
    order = request.GET.get('order', 'desc')
    if cache_data:
        sessions, gsp_by_session = cache_data['sessions'], cache_data['gsp_by_session']
    else:
        sessions = GameSession.objects.select_related('mission').order_by('-start_time')
        session_ids = [s.id for s in sessions]
        all_gsp = GameSessionPlayer.objects.filter(session_id__in=session_ids)
        gsp_by_session = defaultdict(list)
        for gsp in all_gsp:
            gsp_by_session[gsp.session_id].append(gsp)
        cache.set(SESSION_LIST_CACHE_KEY, {'sessions': sessions, 'gsp_by_session': gsp_by_session}, 300)

    sort_fields = {
        'nom': 'name',
        'carte': 'map',
        'date': 'start_time',
        'duration': 'end_time',
        'verdict': 'verdict',
    }
    if sort == 'duration':
        sessions = list(sessions)
        sessions.sort(
            key=lambda s: (
                s.end_time is not None,
                (s.end_time - s.start_time).total_seconds() if s.end_time else 0,
            ),
            reverse=(order == 'desc'),
        )
    else:
        attr = sort_fields.get(sort, 'start_time')
        sessions = sorted(
            sessions,
            key=lambda s: getattr(s, attr),
            reverse=(order == 'desc'),
        )

    map_display_cache = get_map_display_cache()
    map_displays = {}
    for session in sessions:
        code = session.map
        map_displays[session.id] = map_display_cache.get(code, code)

    sessions_data = []
    for session in sessions:
        duration_min = None
        if session.end_time:
            duration_min = int((session.end_time - session.start_time).total_seconds() // 60)
        gsps = gsp_by_session[session.id]
        players_count = len(gsps)
        vivant_count = sum(1 for gsp in gsps if gsp.status == 'VIVANT')
        sessions_data.append({
            'id': session.id,
            'name': session.name,
            'map': session.map,
            'start_time': session.start_time,
            'duration_min': duration_min,
            'verdict': session.verdict,
            'verdict_display': session.get_verdict_display(),
            'players_count': players_count,
            'vivant_count': vivant_count,
        })
    return render(request, 'gdc_storm/session_list.html', {
        'sessions_data': sessions_data,
        'map_displays': map_displays,
        'sort': sort,
        'order': order
    })

def _verdict_choices_for_user(user, session):
    """Participants : pas de @EFFACER ; superuser : tous les verdicts."""
    if user.is_authenticated and user.is_superuser:
        return session.VERDICT_CHOICES
    return [
        choice for choice in session.VERDICT_CHOICES
        if choice[0] != session.VERDICT_EFFACER
    ]


@require_http_methods(['GET', 'POST'])
def session_detail(request, session_id):
    session = get_object_or_404(GameSession, id=session_id)
    # Gestion du POST pour l'édition du statut des joueurs
    if request.method == 'POST' and 'edit_status' in request.POST and request.user.is_superuser:
        updated = 0
        for gsp in session.players.all():
            status_key = f'status_{gsp.id}'
            new_status = request.POST.get(status_key)
            if new_status in ['VIVANT', 'MORT'] and gsp.status != new_status:
                gsp.status = new_status
                gsp.save()
                updated += 1
        if updated:
            invalidate_session_list_cache()
            messages.success(request, f"Statut de {updated} joueur(s) mis à jour.")
        else:
            messages.info(request, "Aucun changement de statut détecté.")
    missions_candidates = []
    show_associate_btn = False
    no_mission_found = False
    # Droits d'édition verdict : superuser, ou participant si encore INCONNU
    user_can_edit_verdict = False
    if request.user.is_authenticated:
        if request.user.is_superuser:
            user_can_edit_verdict = True
        elif session.verdict == session.VERDICT_INCONNU:
            player_ids = list(request.user.players.values_list('id', flat=True))
            if player_ids and GameSessionPlayer.objects.filter(
                session_id=session.id, player_id__in=player_ids
            ).exists():
                user_can_edit_verdict = True
    # Gestion du POST pour le verdict
    if request.method == 'POST' and 'set_verdict' in request.POST:
        if not user_can_edit_verdict:
            messages.error(request, "Modification du verdict non autorisée.")
        else:
            verdict = request.POST.get('verdict')
            allowed_verdicts = dict(_verdict_choices_for_user(request.user, session))
            if verdict in allowed_verdicts:
                session.verdict = verdict
                session.save()
                invalidate_session_list_cache()
                messages.success(request, "Verdict mis à jour.")
                # Recalcul après changement
                user_can_edit_verdict = request.user.is_authenticated and (
                    request.user.is_superuser
                    or (
                        session.verdict == session.VERDICT_INCONNU
                        and GameSessionPlayer.objects.filter(
                            session_id=session.id,
                            player_id__in=request.user.players.values_list('id', flat=True),
                        ).exists()
                    )
                )
            else:
                messages.error(request, "Valeur de verdict invalide.")
    # Préparation des infos pour le template amélioré
    map_display = get_map_display(session.map)
    mission_name = session.name
    duration_min = None
    if session.start_time and session.end_time:
        duration_min = int((session.end_time - session.start_time).total_seconds() // 60)
    players_count = session.players.count()
    vivant_count = session.players.filter(status='VIVANT').count()
    session_players = session.players.select_related('player').all()
    if session.mission is None:
        from .utils import normalize_map_code, strip_mission_version
        # Enlève la version et le préfixe CPC-XX[YY]- (matching UI, casse ignorée)
        name_no_version, _ = strip_mission_version(session.name or '')
        name_clean = re.sub(
            r'^CPC-\w+\[\d+\]-', '', name_no_version, flags=re.IGNORECASE
        ).casefold()
        map_norm = normalize_map_code(session.map)
        all_missions = (
            Mission.objects.filter(map__iexact=map_norm) if map_norm else Mission.objects.none()
        )
        missions_candidates = [
            m for m in all_missions
            if re.sub(
                r'^CPC-\w+\[\d+\]-', '', m.name or '', flags=re.IGNORECASE
            ).casefold() == name_clean
        ]
        show_associate_btn = request.user.is_authenticated and request.user.is_superuser
        no_mission_found = len(missions_candidates) == 0
        if request.method == 'POST' and 'mission_id' in request.POST:
            mission_id = request.POST.get('mission_id')
            candidate_ids = {m.id for m in missions_candidates}
            if not show_associate_btn:
                messages.error(request, "Association non autorisée.")
            else:
                try:
                    mid = int(mission_id)
                except (TypeError, ValueError):
                    mid = None
                if mid not in candidate_ids:
                    messages.error(request, "Mission non candidate pour cette session.")
                else:
                    try:
                        mission = Mission.objects.get(id=mid)
                        session.mission = mission
                        if map_norm:
                            session.map = map_norm
                        session.save()
                        invalidate_session_list_cache()
                        messages.success(request, "Mission associée avec succès à la session.")
                        return redirect('session_detail', session_id=session.id)
                    except Mission.DoesNotExist:
                        messages.error(request, "Mission introuvable.")
    return render(request, 'gdc_storm/session_detail.html', {
        'session': session,
        'missions_candidates': missions_candidates,
        'show_associate_btn': show_associate_btn,
        'no_mission_found': session.mission is None and no_mission_found,
        'map_display': map_display,
        'mission_name': mission_name,
        'duration_min': duration_min,
        'players_count': players_count,
        'vivant_count': vivant_count,
        'session_players': session_players,
        'user_can_edit_verdict': user_can_edit_verdict,
        'verdict_choices': _verdict_choices_for_user(request.user, session),
    })

def orphan_sessions(request):
    sessions = annotate_session_player_counts(
        GameSession.objects.filter(mission__isnull=True)
    ).order_by('-start_time')
    sessions_data = []
    for s in sessions:
        duration_min = None
        if s.start_time and s.end_time:
            duration_min = int((s.end_time - s.start_time).total_seconds() // 60)
        sessions_data.append({
            'id': s.id,
            'name': s.name,
            'map': s.map,
            'start_time': s.start_time,
            'duration_min': duration_min,
            'verdict': s.verdict,
            'get_verdict_display': s.get_verdict_display(),
            'players_count': s.players_count,
            'vivant_count': s.vivant_count,
        })
    return render(request, 'gdc_storm/orphan_sessions.html', {'sessions': sessions_data})

def map_detail(request, map_id):
    map_obj = get_object_or_404(MapName, id=map_id)
    # Missions dont le champ map == code_name de la carte
    missions = Mission.objects.filter(map=map_obj.code_name).order_by('-id')
    # Sessions dont le champ map == code_name de la carte
    sessions = annotate_session_player_counts(
        GameSession.objects.filter(map=map_obj.code_name)
    ).order_by('-start_time')
    sessions_data = []
    for session in sessions:
        duration_min = None
        if session.start_time and session.end_time:
            duration_min = int((session.end_time - session.start_time).total_seconds() // 60)
        sessions_data.append({
            'id': session.id,
            'name': session.name,
            'start_time': session.start_time,
            'duration_min': duration_min,
            'verdict': session.verdict,
            'verdict_display': session.get_verdict_display(),
            'players_count': session.players_count,
            'vivant_count': session.vivant_count,
        })
    sessions_qs = GameSession.objects.filter(map=map_obj.code_name)
    win_stats = session_win_rate(sessions_qs)
    dur_stats = duration_stats(sessions)
    avg_stats = avg_players_survivors(sessions_data)
    gsp_qs = GameSessionPlayer.objects.filter(session__map=map_obj.code_name)
    roles = role_breakdown(gsp_qs, limit=5)
    top_missions = top_missions_for_sessions(sessions_qs, limit=5)
    context = {
        'map': map_obj,
        'missions': missions,
        'sessions_data': sessions_data,
        'missions_count': missions.count(),
        'sessions_count': sessions.count(),
        'detail_stats': {
            **win_stats,
            **dur_stats,
            **avg_stats,
            **roles,
            'top_missions': top_missions,
        },
    }
    return render(request, 'gdc_storm/map_detail.html', context)

def map_list(request):
    sort = request.GET.get('sort', 'display_name')
    order = request.GET.get('order', 'asc')
    maps = list(MapName.objects.all())
    mission_counts = dict(
        Mission.objects.values('map').annotate(c=Count('id')).values_list('map', 'c')
    )
    session_counts = dict(
        GameSession.objects.values('map').annotate(c=Count('id')).values_list('map', 'c')
    )
    map_data = []
    for m in maps:
        map_data.append({
            'id': m.id,
            'display_name': m.display_name,
            'code_name': m.code_name,
            'missions_count': mission_counts.get(m.code_name, 0),
            'sessions_count': session_counts.get(m.code_name, 0),
        })
    # Tri dynamique
    reverse_order = (order == 'desc')
    if sort == 'display_name' or sort == 'nom':
        map_data.sort(key=lambda x: (x['display_name'] or '').lower(), reverse=reverse_order)
    elif sort == 'code_name' or sort == 'origine':
        map_data.sort(key=lambda x: (x['code_name'] or '').lower(), reverse=reverse_order)
    elif sort == 'missions' or sort == 'missions_count':
        map_data.sort(key=lambda x: x['missions_count'], reverse=reverse_order)
    elif sort == 'sessions' or sort == 'sessions_count':
        map_data.sort(key=lambda x: x['sessions_count'], reverse=reverse_order)
    return render(request, 'gdc_storm/map_list.html', {
        'maps': map_data,
        'sort': sort,
        'order': order
    })


def _build_stats_context():
    """Agrège toutes les statistiques globales pour la page Stats."""
    map_display_cache = {m.code_name: m.display_name for m in MapName.objects.all()}

    # --- KPIs ---
    missions_count = Mission.objects.count()
    sessions_count = GameSession.objects.count()
    players_count = Player.objects.count()
    users_count = User.objects.filter(is_active=True).count()

    missions_by_status = {
        row['status']: row['c']
        for row in Mission.objects.values('status').annotate(c=Count('id'))
    }
    status_labels = dict(Mission.STATUS_CHOICES)
    missions_status_chart = {
        'labels': [status_labels.get(k, k) for k in missions_by_status.keys()],
        'data': list(missions_by_status.values()),
    }

    missions_by_type = {
        row['type']: row['c']
        for row in Mission.objects.values('type').annotate(c=Count('id'))
    }
    type_labels = dict(Mission.TYPE_CHOICES)
    missions_type_chart = {
        'labels': [type_labels.get(k, k) for k in missions_by_type.keys()],
        'data': list(missions_by_type.values()),
    }

    sessions_by_verdict = {
        row['verdict']: row['c']
        for row in GameSession.objects.values('verdict').annotate(c=Count('id'))
    }
    verdict_labels = dict(GameSession.VERDICT_CHOICES)
    sessions_verdict_chart = {
        'labels': [verdict_labels.get(k, k) for k in sessions_by_verdict.keys()],
        'data': list(sessions_by_verdict.values()),
    }

    succes_count = sessions_by_verdict.get(GameSession.VERDICT_SUCCES, 0)
    echec_count = sessions_by_verdict.get(GameSession.VERDICT_ECHEC, 0)
    decisive_count = succes_count + echec_count
    global_win_rate = round(100 * succes_count / decisive_count) if decisive_count else None

    # Durées (sessions avec end_time)
    finished = list(
        GameSession.objects.filter(end_time__isnull=False)
        .only('id', 'name', 'start_time', 'end_time', 'map')
    )
    durations_sec = []
    longest_session = None
    longest_sec = -1
    for s in finished:
        sec = (s.end_time - s.start_time).total_seconds()
        if sec < 0:
            continue
        durations_sec.append(sec)
        if sec > longest_sec:
            longest_sec = sec
            longest_session = s
    total_playtime_sec = int(sum(durations_sec)) if durations_sec else 0
    avg_duration_min = round(sum(durations_sec) / len(durations_sec) / 60) if durations_sec else None
    total_playtime_hours = round(total_playtime_sec / 3600, 1) if total_playtime_sec else 0

    gsp_total = GameSessionPlayer.objects.count()
    avg_players_per_session = round(gsp_total / sessions_count, 1) if sessions_count else None

    never_played_count = Mission.objects.annotate(
        played=Count('game_sessions')
    ).filter(played=0).count()

    # --- Évolution mensuelle (24 mois) ---
    now = timezone.now()
    since = now - timedelta(days=730)
    sessions_monthly_qs = (
        GameSession.objects.filter(start_time__gte=since)
        .annotate(month=TruncMonth('start_time'))
        .values('month')
        .annotate(c=Count('id'))
        .order_by('month')
    )
    missions_monthly_qs = (
        Mission.objects.filter(publication_date__gte=since)
        .annotate(month=TruncMonth('publication_date'))
        .values('month')
        .annotate(c=Count('id'))
        .order_by('month')
    )
    sessions_by_month = {
        (row['month'].year, row['month'].month): row['c']
        for row in sessions_monthly_qs
        if row['month']
    }
    missions_by_month = {
        (row['month'].year, row['month'].month): row['c']
        for row in missions_monthly_qs
        if row['month']
    }
    month_labels = []
    sessions_month_data = []
    missions_month_data = []
    cursor = (since.replace(day=1) if since.day != 1 else since)
    # Align cursor to first of month
    cursor = cursor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cursor <= end_month:
        key = (cursor.year, cursor.month)
        month_labels.append(f"{month_abbr[cursor.month]} {cursor.year}")
        sessions_month_data.append(sessions_by_month.get(key, 0))
        missions_month_data.append(missions_by_month.get(key, 0))
        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)

    monthly_chart = {
        'labels': month_labels,
        'sessions': sessions_month_data,
        'missions': missions_month_data,
    }

    # --- Jour de la semaine / heure ---
    # ExtractWeekDay: 1=Sunday … 7=Saturday (Django)
    weekday_names = {
        1: 'Dimanche', 2: 'Lundi', 3: 'Mardi', 4: 'Mercredi',
        5: 'Jeudi', 6: 'Vendredi', 7: 'Samedi',
    }
    weekday_order = [2, 3, 4, 5, 6, 7, 1]  # Lundi → Dimanche
    weekday_raw = {
        row['wd']: row['c']
        for row in GameSession.objects.annotate(wd=ExtractWeekDay('start_time'))
        .values('wd').annotate(c=Count('id'))
        if row['wd'] is not None
    }
    weekday_chart = {
        'labels': [weekday_names[d] for d in weekday_order],
        'data': [weekday_raw.get(d, 0) for d in weekday_order],
    }
    most_active_weekday = None
    if weekday_raw:
        best_wd = max(weekday_raw, key=weekday_raw.get)
        most_active_weekday = weekday_names.get(best_wd, str(best_wd))

    hour_raw = {
        row['hour']: row['c']
        for row in GameSession.objects.annotate(hour=ExtractHour('start_time'))
        .values('hour').annotate(c=Count('id'))
        if row['hour'] is not None
    }
    hour_chart = {
        'labels': [f"{h:02d}h" for h in range(24)],
        'data': [hour_raw.get(h, 0) for h in range(24)],
    }

    # --- Top missions ---
    top_missions_qs = (
        Mission.objects.annotate(
            played_count=Count('game_sessions'),
            succes_count=Count(
                'game_sessions',
                filter=Q(game_sessions__verdict=GameSession.VERDICT_SUCCES),
            ),
            echec_count=Count(
                'game_sessions',
                filter=Q(game_sessions__verdict=GameSession.VERDICT_ECHEC),
            ),
        )
        .filter(played_count__gt=0)
        .order_by('-played_count')[:10]
    )
    top_missions = []
    for m in top_missions_qs:
        decisive = m.succes_count + m.echec_count
        win_rate = round(100 * m.succes_count / decisive) if decisive else None
        top_missions.append({
            'id': m.id,
            'name': m.name,
            'map_display': map_display_cache.get(m.map, m.map),
            'played_count': m.played_count,
            'win_rate': win_rate,
            'succes_count': m.succes_count,
            'decisive': decisive,
        })

    # --- Top cartes ---
    map_stats_raw = (
        GameSession.objects.values('map')
        .annotate(
            sessions_count=Count('id'),
            succes_count=Count('id', filter=Q(verdict=GameSession.VERDICT_SUCCES)),
            echec_count=Count('id', filter=Q(verdict=GameSession.VERDICT_ECHEC)),
        )
        .order_by('-sessions_count')
    )
    top_maps = []
    maps_winrate = []
    for row in map_stats_raw:
        code = row['map'] or ''
        display = map_display_cache.get(code, code or '—')
        decisive = row['succes_count'] + row['echec_count']
        win_rate = round(100 * row['succes_count'] / decisive) if decisive else None
        entry = {
            'code': code,
            'display': display,
            'sessions_count': row['sessions_count'],
            'win_rate': win_rate,
            'succes_count': row['succes_count'],
            'decisive': decisive,
        }
        if len(top_maps) < 10:
            top_maps.append(entry)
        if decisive >= STATS_MIN_DECISIVE_FOR_MAP_WR:
            maps_winrate.append(entry)

    maps_best_winrate = sorted(
        maps_winrate, key=lambda x: (x['win_rate'] is not None, x['win_rate'] or 0), reverse=True
    )[:10]
    maps_worst_winrate = sorted(
        maps_winrate, key=lambda x: (x['win_rate'] is not None, x['win_rate'] if x['win_rate'] is not None else 999)
    )[:10]

    most_played_map = top_maps[0]['display'] if top_maps else None

    # --- Top joueurs ---
    top_players_qs = (
        Player.objects.annotate(
            sessions_count=Count('game_sessions__session', distinct=True),
        )
        .filter(sessions_count__gt=0)
        .order_by('-sessions_count')[:10]
    )
    top_players = [
        {'id': p.id, 'name': p.name, 'sessions_count': p.sessions_count}
        for p in top_players_qs
    ]

    # Ratio vivant/mort (seuil minimum)
    player_status = (
        GameSessionPlayer.objects.values('player_id', 'player__name')
        .annotate(
            sessions_count=Count('session', distinct=True),
            alive=Count('id', filter=Q(status='VIVANT')),
            dead=Count('id', filter=Q(status='MORT')),
        )
        .filter(sessions_count__gte=STATS_MIN_SESSIONS_FOR_RATIO)
    )
    survival_ranking = []
    for row in player_status:
        dead = row['dead']
        alive = row['alive']
        if dead > 0:
            ratio = round(alive / dead, 2)
        else:
            ratio = float('inf') if alive > 0 else 0
        survival_ranking.append({
            'id': row['player_id'],
            'name': row['player__name'],
            'sessions_count': row['sessions_count'],
            'alive': alive,
            'dead': dead,
            'ratio': ratio,
            'ratio_display': '∞' if ratio == float('inf') else ratio,
        })
    survival_ranking.sort(
        key=lambda x: (1, 0) if x['ratio'] == float('inf') else (0, x['ratio']),
        reverse=True,
    )
    top_survival = survival_ranking[:10]

    # --- Rôles ---
    top_roles_raw = (
        GameSessionPlayer.objects.exclude(role='')
        .values('role')
        .annotate(c=Count('id'))
        .order_by('-c')[:15]
    )
    top_roles = [{'role': r['role'], 'count': r['c']} for r in top_roles_raw]
    most_popular_role = top_roles[0]['role'] if top_roles else None

    role_cat_map = {
        rc.role_name: rc.category for rc in RoleCategory.objects.all()
    }
    category_counts = defaultdict(int)
    for r in GameSessionPlayer.objects.exclude(role='').values('role').annotate(c=Count('id')):
        cat = role_cat_map.get(r['role'], r['role'])
        category_counts[cat] += r['c']
    top_role_categories = sorted(
        [{'category': k, 'count': v} for k, v in category_counts.items()],
        key=lambda x: -x['count'],
    )[:15]

    # --- Top propriétaires (compte MM lié, pas le champ texte PBO authors) ---
    top_authors_raw = (
        Mission.objects.filter(user__isnull=False)
        .values('user__username')
        .annotate(c=Count('id'))
        .order_by('-c')[:10]
    )
    top_authors = [{'authors': a['user__username'], 'count': a['c']} for a in top_authors_raw]

    # Fun facts
    longest_session_info = None
    if longest_session:
        longest_session_info = {
            'id': longest_session.id,
            'name': longest_session.name,
            'duration_min': int(longest_sec // 60),
            'map_display': map_display_cache.get(longest_session.map, longest_session.map),
        }

    return {
        # KPIs
        'missions_count': missions_count,
        'sessions_count': sessions_count,
        'players_count': players_count,
        'users_count': users_count,
        'decisive_count': decisive_count,
        'succes_count': succes_count,
        'echec_count': echec_count,
        'global_win_rate': global_win_rate,
        'total_playtime_hours': total_playtime_hours,
        'avg_duration_min': avg_duration_min,
        'avg_players_per_session': avg_players_per_session,
        'never_played_count': never_played_count,
        # Charts (JSON-serializable)
        'missions_status_chart': missions_status_chart,
        'missions_type_chart': missions_type_chart,
        'sessions_verdict_chart': sessions_verdict_chart,
        'monthly_chart': monthly_chart,
        'weekday_chart': weekday_chart,
        'hour_chart': hour_chart,
        # Rankings
        'top_missions': top_missions,
        'top_maps': top_maps,
        'maps_best_winrate': maps_best_winrate,
        'maps_worst_winrate': maps_worst_winrate,
        'top_players': top_players,
        'top_survival': top_survival,
        'top_roles': top_roles,
        'top_role_categories': top_role_categories,
        'top_authors': top_authors,
        # Fun facts
        'longest_session': longest_session_info,
        'most_played_map': most_played_map,
        'most_popular_role': most_popular_role,
        'most_active_weekday': most_active_weekday,
        'min_sessions_for_ratio': STATS_MIN_SESSIONS_FOR_RATIO,
        'min_decisive_for_map_wr': STATS_MIN_DECISIVE_FOR_MAP_WR,
    }


def stats(request):
    """Page publique de statistiques globales GDC Storm."""
    context = cache.get(STATS_CACHE_KEY)
    if context is None:
        context = _build_stats_context()
        cache.set(STATS_CACHE_KEY, context, STATS_CACHE_TTL)
    return render(request, 'gdc_storm/stats.html', context)


@login_required
def role_categories(request):
    """Interface admin : fusion / édition des catégories de rôles."""
    forbidden = _require_superuser(request)
    if forbidden:
        return forbidden

    ROLE_USAGE_TTL = 300
    PAGE_SIZE = 100

    def _invalidate_role_caches():
        cache.delete(STATS_CACHE_KEY)
        cache.delete(ROLE_USAGE_CACHE_KEY)

    def _get_role_counts():
        counts = cache.get(ROLE_USAGE_CACHE_KEY)
        if counts is not None:
            return counts
        counts = {
            row['role']: row['c']
            for row in GameSessionPlayer.objects.exclude(role='')
            .values('role')
            .annotate(c=Count('id'))
        }
        cache.set(ROLE_USAGE_CACHE_KEY, counts, ROLE_USAGE_TTL)
        return counts

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'sync_roles':
            # Sync léger : DISTINCT seulement (pas de COUNT), hors du chemin de lecture habituel
            existing_names = set(RoleCategory.objects.values_list('role_name', flat=True))
            distinct_roles = (
                GameSessionPlayer.objects.exclude(role='')
                .values_list('role', flat=True)
                .distinct()
            )
            missing = [
                RoleCategory(role_name=n, category=n)
                for n in distinct_roles
                if n not in existing_names
            ]
            if missing:
                RoleCategory.objects.bulk_create(missing, ignore_conflicts=True, batch_size=500)
                messages.success(request, f"{len(missing)} nouveau(x) rôle(s) synchronisé(s).")
            else:
                messages.info(request, "Aucun nouveau rôle à synchroniser.")
            _invalidate_role_caches()
            return redirect('role_categories')

        if action == 'merge':
            ids = request.POST.getlist('role_ids')
            target = (request.POST.get('target_category') or '').strip()
            if not ids:
                messages.error(request, "Sélectionne au moins un rôle à fusionner.")
            elif not target:
                messages.error(request, "Indique une catégorie cible.")
            else:
                updated = RoleCategory.objects.filter(id__in=ids).update(category=target)
                _invalidate_role_caches()
                messages.success(
                    request,
                    f"{updated} rôle(s) fusionné(s) dans « {target} ».",
                )
            return redirect(request.get_full_path() if request.GET else 'role_categories')

        if action == 'update_one':
            role_id = request.POST.get('role_id')
            new_cat = (request.POST.get('category') or '').strip()
            next_url = request.POST.get('next') or reverse('role_categories')
            if not role_id or not new_cat:
                messages.error(request, "Catégorie invalide.")
            else:
                rc = get_object_or_404(RoleCategory, id=role_id)
                old = rc.category
                if old != new_cat:
                    rc.category = new_cat
                    rc.save(update_fields=['category'])
                    _invalidate_role_caches()
                    messages.success(
                        request,
                        f"« {rc.role_name} » : « {old} » → « {new_cat} ».",
                    )
                else:
                    messages.info(request, "Aucun changement.")
            return safe_internal_redirect(request, next_url, 'role_categories')

        if action == 'reset_one':
            role_id = request.POST.get('role_id')
            next_url = request.POST.get('next') or reverse('role_categories')
            rc = get_object_or_404(RoleCategory, id=role_id)
            if rc.category != rc.role_name:
                rc.category = rc.role_name
                rc.save(update_fields=['category'])
                _invalidate_role_caches()
                messages.success(
                    request,
                    f"« {rc.role_name} » réinitialisé (catégorie = nom brut).",
                )
            return safe_internal_redirect(request, next_url, 'role_categories')

    q = (request.GET.get('q') or '').strip()
    filter_uncategorized = request.GET.get('uncategorized') == '1'
    sort = request.GET.get('sort', 'usage')
    page_number = request.GET.get('page', '1')

    role_counts = _get_role_counts()

    qs = RoleCategory.objects.all()
    if q:
        qs = qs.filter(Q(role_name__icontains=q) | Q(category__icontains=q))
    if filter_uncategorized:
        qs = qs.filter(category=F('role_name'))

    roles = list(qs.only('id', 'role_name', 'category'))
    for rc in roles:
        rc.usage_count = role_counts.get(rc.role_name, 0)
        rc.is_mapped = rc.category != rc.role_name

    if sort == 'name':
        roles.sort(key=lambda r: (r.role_name or '').casefold())
    elif sort == 'category':
        roles.sort(key=lambda r: ((r.category or '').casefold(), (r.role_name or '').casefold()))
    else:
        # usage desc (défaut) — les rôles les plus joués en premier
        roles.sort(key=lambda r: (-r.usage_count, (r.role_name or '').casefold()))

    mapped_count = sum(1 for r in roles if r.is_mapped)
    roles_count = len(roles)

    paginator = Paginator(roles, PAGE_SIZE)
    page_obj = paginator.get_page(page_number)

    existing_categories = sorted(
        {r.category for r in roles if r.category},
        key=lambda x: x.casefold(),
    )

    return render(request, 'gdc_storm/role_categories.html', {
        'roles': page_obj,
        'page_obj': page_obj,
        'roles_count': roles_count,
        'mapped_count': mapped_count,
        'q': q,
        'filter_uncategorized': filter_uncategorized,
        'sort': sort,
        'existing_categories': existing_categories,
    })


def _merge_players_into(keep: Player, sources):
    """Réaffecte sessions et liens User des joueurs sources vers keep, puis les supprime."""
    Through = Player.users.through
    moved_sessions = 0
    dropped_conflicts = 0
    for source in sources:
        if source.id == keep.id:
            continue
        for gsp in GameSessionPlayer.objects.filter(player_id=source.id).iterator():
            conflict = GameSessionPlayer.objects.filter(
                session_id=gsp.session_id, player_id=keep.id
            ).exists()
            if conflict:
                gsp.delete()
                dropped_conflicts += 1
            else:
                gsp.player_id = keep.id
                gsp.save(update_fields=['player_id'])
                moved_sessions += 1
        for link in Through.objects.filter(player_id=source.id):
            Through.objects.get_or_create(player_id=keep.id, user_id=link.user_id)
            link.delete()
        source.delete()
    return moved_sessions, dropped_conflicts


@login_required
def player_admin(request):
    """Interface admin : fusion de joueurs et liaison à des utilisateurs."""
    forbidden = _require_superuser(request)
    if forbidden:
        return forbidden

    PAGE_SIZE = 100

    def _invalidate_player_caches():
        cache.delete(STATS_CACHE_KEY)
        cache.delete(SESSION_LIST_CACHE_KEY)

    if request.method == 'POST':
        action = request.POST.get('action', '')
        next_url = request.POST.get('next') or reverse('player_admin')

        if action == 'merge':
            ids = request.POST.getlist('player_ids')
            target_id = request.POST.get('target_player_id')
            new_name = (request.POST.get('new_name') or '').strip()
            if len(ids) < 2:
                messages.error(request, "Sélectionne au moins deux joueurs à fusionner.")
            elif not target_id:
                messages.error(request, "Indique le joueur cible (conservé).")
            elif target_id not in ids:
                messages.error(request, "Le joueur cible doit faire partie de la sélection.")
            else:
                try:
                    with transaction.atomic():
                        keep = Player.objects.select_for_update().get(id=target_id)
                        sources = list(
                            Player.objects.select_for_update().filter(id__in=ids).exclude(id=keep.id)
                        )
                        if new_name and new_name != keep.name:
                            if Player.objects.filter(name=new_name).exclude(id=keep.id).exists():
                                raise IntegrityError(f"Le nom « {new_name} » est déjà pris.")
                            keep.name = new_name
                            keep.save(update_fields=['name'])
                        moved, dropped = _merge_players_into(keep, sources)
                    _invalidate_player_caches()
                    msg = (
                        f"Fusion vers « {keep.name} » : {len(sources)} joueur(s) fusionné(s), "
                        f"{moved} participation(s) déplacée(s)."
                    )
                    if dropped:
                        msg += f" {dropped} doublon(s) de session ignoré(s)."
                    messages.success(request, msg)
                except Player.DoesNotExist:
                    messages.error(request, "Joueur introuvable.")
                except IntegrityError as exc:
                    messages.error(request, str(exc))
            return safe_internal_redirect(
                request,
                next_url if request.GET else reverse('player_admin'),
                'player_admin',
            )

        if action == 'link_user':
            ids = request.POST.getlist('player_ids')
            user_id = request.POST.get('user_id')
            if not ids:
                messages.error(request, "Sélectionne au moins un joueur.")
            elif not user_id:
                messages.error(request, "Choisis un utilisateur à lier.")
            else:
                user_obj = User.objects.filter(id=user_id).first()
                if not user_obj:
                    messages.error(request, "Utilisateur introuvable.")
                else:
                    players = list(Player.objects.filter(id__in=ids))
                    for p in players:
                        p.users.add(user_obj)
                    _invalidate_player_caches()
                    messages.success(
                        request,
                        f"{len(players)} joueur(s) lié(s) à « {user_obj.username} ».",
                    )
            return safe_internal_redirect(
                request,
                next_url if request.GET else reverse('player_admin'),
                'player_admin',
            )

        if action == 'unlink_user':
            player_id = request.POST.get('player_id')
            user_id = request.POST.get('user_id')
            player = get_object_or_404(Player, id=player_id)
            user_obj = get_object_or_404(User, id=user_id)
            player.users.remove(user_obj)
            _invalidate_player_caches()
            messages.success(
                request,
                f"« {player.name} » délié de « {user_obj.username} ».",
            )
            return safe_internal_redirect(request, next_url, 'player_admin')

    q = (request.GET.get('q') or '').strip()
    filter_unlinked = request.GET.get('unlinked') == '1'
    sort = request.GET.get('sort', 'name')
    page_number = request.GET.get('page', '1')

    qs = Player.objects.annotate(
        users_count=Count('users', distinct=True),
        sessions_count=Count('game_sessions', distinct=True),
    ).prefetch_related('users')

    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(users__username__icontains=q)
        ).distinct()
    if filter_unlinked:
        qs = qs.filter(users_count=0)

    if sort == 'sessions':
        qs = qs.order_by('-sessions_count', 'name')
    elif sort == 'users':
        qs = qs.order_by('-users_count', 'name')
    else:
        qs = qs.order_by('name')

    players_count = qs.count()
    linked_count = qs.filter(users_count__gt=0).count()

    paginator = Paginator(qs, PAGE_SIZE)
    page_obj = paginator.get_page(page_number)

    all_users = User.objects.order_by('username').only('id', 'username')

    return render(request, 'gdc_storm/player_admin.html', {
        'players': page_obj,
        'page_obj': page_obj,
        'players_count': players_count,
        'linked_count': linked_count,
        'q': q,
        'filter_unlinked': filter_unlinked,
        'sort': sort,
        'all_users': all_users,
    })
