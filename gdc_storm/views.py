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
        elif not new_password1 or len(new_password1) < 6:
            messages.error(request, "Le nouveau mot de passe doit contenir au moins 6 caractères.")
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
from django.db import IntegrityError
from django.db.models import Count, Max, Q
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.core.files.storage import default_storage
from django.views.decorators.http import require_POST, require_http_methods
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.contrib.auth.models import Group, User
from django.conf import settings
from django.utils.dateparse import parse_datetime
from functools import wraps

UPLOAD_ANALYZE_MAX_FILES = 100
RECUP_USERNAME = 'GDC-RECUP'
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

from .models import Mission, MapName, Player, GameSession, GameSessionPlayer, ApiToken
from .models import LegacyRole, LegacyMission, LegacyImportError, LegacyGameSession, LegacyMapNames, LegacyGameSessionPlayerRole, LegacyPlayers
from .forms import MissionStatusForm
from gdc_storm.utils import (
    parse_mission_filename,
    recup_parse_mission_filename,
    invalidate_session_list_cache,
    SESSION_LIST_CACHE_KEY,
)
from gdc_storm.pbo_extract import is_sqm_binarized, extract_mission_data_from_pbo, extract_briefing_from_pbo


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


def get_upload_temp_dir():
    temp_dir = os.path.join(tempfile.gettempdir(), 'gdc_storm')
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def is_safe_upload_temp_path(temp_file_path):
    """Refuse les chemins hors du répertoire temp gdc_storm (anti path traversal)."""
    if not temp_file_path:
        return False
    temp_dir = os.path.realpath(get_upload_temp_dir())
    real_path = os.path.realpath(temp_file_path)
    try:
        return os.path.commonpath([temp_dir, real_path]) == temp_dir and os.path.isfile(real_path)
    except ValueError:
        return False


def save_uploaded_pbo_to_temp(uploaded_file):
    """Sauve un UploadedFile dans le répertoire temp et retourne (temp_file_path, temp_file_name, filename)."""
    raw_name = uploaded_file.name or ''
    filename = os.path.basename(raw_name.replace('\\', '/'))
    if not filename or filename in ('.', '..'):
        raise ValueError("Nom de fichier upload invalide.")
    temp_file_name = f"{uuid.uuid4()}_{filename}"
    temp_dir = get_upload_temp_dir()
    temp_file_path = os.path.join(temp_dir, temp_file_name)
    if os.path.dirname(os.path.realpath(temp_file_path)) != os.path.realpath(temp_dir):
        raise ValueError("Chemin temporaire hors répertoire autorisé.")
    with open(temp_file_path, 'wb+') as destination:
        for chunk in uploaded_file.chunks():
            destination.write(chunk)
    return temp_file_path, temp_file_name, filename


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


@login_required
def upload_mission(request):
    """Page d'upload multi-PBO (analyse + récap + commit via endpoints JSON)."""
    if not user_is_mission_maker(request.user):
        return HttpResponse("Vous n'avez pas le droit de publier une mission.", status=403)
    clean_temp_files(get_upload_temp_dir())
    return render(request, 'gdc_storm/upload_mission.html', {
        'max_files': UPLOAD_ANALYZE_MAX_FILES,
    })


@login_required
@require_POST
def upload_analyze(request):
    """Analyse un ou plusieurs PBO et propose une action par fichier."""
    if not user_is_mission_maker(request.user):
        return _mission_maker_forbidden_json()

    clean_temp_files(get_upload_temp_dir())
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
            temp_file_path, temp_file_name, filename = save_uploaded_pbo_to_temp(uploaded)
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

        if not is_safe_upload_temp_path(temp_file_path):
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
    """Page de récupération multi-PBO depuis le cache joueur (Mission Maker requis)."""
    if not user_is_mission_maker(request.user):
        return HttpResponse("Vous n'avez pas le droit de récupérer une mission.", status=403)
    clean_temp_files(get_upload_temp_dir())
    return render(request, 'gdc_storm/recup_missions.html', {
        'max_files': UPLOAD_ANALYZE_MAX_FILES,
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


@login_required
@require_POST
def recup_analyze(request):
    """Analyse un ou plusieurs PBO pour la récupération (même logique doublons/versions que l'upload)."""
    if not user_is_mission_maker(request.user):
        return _mission_maker_forbidden_json()
    clean_temp_files(get_upload_temp_dir())
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
            temp_file_path, temp_file_name, filename = save_uploaded_pbo_to_temp(uploaded)
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

        if not is_safe_upload_temp_path(temp_file_path):
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
        'derniere': 'last_played_at',
        'id': 'id',
    }
    sort_field = sort_fields.get(sort, 'id')

    # Ajoute les infos "combien de fois jouée" + "dernière fois jouée"
    missions_qs = Mission.objects.select_related('user').annotate(
        played_count=Count('game_sessions'),
        last_played_at=Max('game_sessions__start_time'),
    )

    if sort == 'carte':
        # Tri personnalisé sur le display_name de la carte
        missions = list(missions_qs)
        map_display = lambda m: get_map_display(m.map).lower() if get_map_display(m.map) else ''
        missions.sort(key=map_display, reverse=(order == 'desc'))
    else:
        if order == 'desc':
            sort_field = '-' + sort_field
        missions = missions_qs.order_by(sort_field)
    map_display_cache = get_map_display_cache()
    map_displays = {m.id: map_display_cache.get(m.map, m.map) for m in missions}
    full_names = {m.id: m.name for m in missions}
    # Prépare un mapping mission_id -> auteur à afficher
    mission_authors_display = {}
    for m in missions:
        if m.authors and m.authors.strip() != 'Non renseigné':
            mission_authors_display[m.id] = m.authors
        elif m.user and hasattr(m.user, 'username') and m.user.username:
            mission_authors_display[m.id] = m.user.username
        else:
            mission_authors_display[m.id] = 'Non renseigné'
    return render(request, 'gdc_storm/mission_list.html', {
        'missions': missions,
        'map_displays': map_displays,
        'full_names': full_names,
        'mission_authors_display': mission_authors_display,
        'sort': sort,
        'order': order
    })

# Mission detail view
def mission_detail(request, mission_id):
    mission = get_object_or_404(Mission, id=mission_id)
    success = request.GET.get('success') == '1'
    map_display = get_map_display(mission.map)
    can_edit_status = request.user.is_superuser or (mission.user == request.user)
    status_form = None
    if can_edit_status:
        if request.method == 'POST' and 'update_status' in request.POST:
            status_form = MissionStatusForm(request.POST, instance=mission)
            if status_form.is_valid():
                status_form.save()
                messages.success(request, "Statut de la mission mis à jour.")
                return redirect('mission_detail', mission_id=mission.id)
        else:
            status_form = MissionStatusForm(instance=mission)
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
    return render(request, 'gdc_storm/mission_detail.html', {
        'mission': mission,
        'success': success,
        'map_display': map_display,
        'can_edit_status': can_edit_status,
        'status_form': status_form,
        'sessions_data': sessions_data,
    })

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

    # Dead alive ratio
    # Out off previous loop to avoid total alive + dead to be higher than total missions played by the player
    alive, dead = 0, 0
    for v in user_status_by_session_id.values():
        if v == 'MORT':
            dead += 1
        else:
            alive += 1
    ratio = round(alive/dead, 2) if dead > 0 else "inf."
    dead_alive_ratio = {"alive" : alive, "dead" : dead, "ratio" : ratio}
    
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
    # Mode récup : autorise temporairement les noms hors convention CPC
    try:
        mission.save(skip_name_check=not strict)
    except IntegrityError:
        return None, (
            "Une mission avec le même nom, carte et nombre de joueurs existe déjà "
            "(création concurrente ou doublon)."
        )
    # Stocke la liste des images de briefing pour suppression ultérieure
    if briefing_images:
        mission.briefing_images = briefing_images
        mission.save(update_fields=['briefing_images'], skip_name_check=not strict)
    try:
        save_pbo_to_storage(temp_file_path, filename)
    except Exception as e:
        return mission, f"Mission créée, mais erreur lors de la sauvegarde du PBO: {e}"
    clear_mission_pbo_missing(mission)
    warning_msg = format_errors(warnings)
    return mission, warning_msg


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
    is_mission_maker = user_is_mission_maker(request.user)
    # preserve_owner : ne change pas le propriétaire (recup/scan), mais n'autorise pas le bypass.
    # Autorisé : admin, propriétaire, ou Mission Maker en mode preserve_owner.
    allowed = is_admin or is_owner or (preserve_owner and is_mission_maker)
    if not allowed:
        return None, "Vous n'avez pas le droit de mettre à jour cette mission (seul le propriétaire, un Mission Maker ou un admin peut le faire)."
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

    # Dead alive ratio
    # Out off previous loop to avoid total alive + dead to be higher than total missions played by the player
    alive, dead = 0, 0
    for v in status_by_session_id.values():
        if v == 'MORT':
            dead += 1
        else:
            alive += 1
    ratio = round(alive/dead, 2) if dead > 0 else "inf."
    dead_alive_ratio = {"alive" : alive, "dead" : dead, "ratio" : ratio}
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
    })

@login_required
def player_mapping(request):
    from .models import Player
    user = request.user
    players = (
        Player.objects.annotate(users_count=Count('users'))
        .prefetch_related('users')
        .order_by('name')
    )
    if request.method == 'POST':
        selected_ids = request.POST.getlist('players')
        # Ne permettre de lier que les Players non liés ou déjà liés à l'utilisateur
        allowed_ids = []
        for p in players:
            linked_ids = {u.id for u in p.users.all()}
            if not linked_ids or user.id in linked_ids:
                allowed_ids.append(str(p.id))
        filtered_ids = [int(pid) for pid in selected_ids if pid in allowed_ids]
        user.players.set(filtered_ids)
        user.save()
        return render(request, 'gdc_storm/player_mapping.html', {'players': players, 'success': True, 'selected_ids': filtered_ids})
    selected_ids = list(user.players.values_list('id', flat=True))
    return render(request, 'gdc_storm/player_mapping.html', {'players': players, 'selected_ids': selected_ids})

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
    # Droits d'édition verdict (mêmes règles UI / serveur)
    user_can_edit_verdict = False
    if request.user.is_authenticated:
        if request.user.is_superuser:
            user_can_edit_verdict = True
        elif session.verdict == session.VERDICT_INCONNU:
            user_can_edit_verdict = True
    # Gestion du POST pour le verdict
    if request.method == 'POST' and 'set_verdict' in request.POST:
        if not user_can_edit_verdict:
            messages.error(request, "Modification du verdict non autorisée.")
        else:
            verdict = request.POST.get('verdict')
            if verdict in dict(GameSession.VERDICT_CHOICES):
                session.verdict = verdict
                session.save()
                invalidate_session_list_cache()
                messages.success(request, "Verdict mis à jour.")
                # Recalcul après changement (ex. INCONNU -> SUCCES retire le droit non-admin)
                user_can_edit_verdict = request.user.is_authenticated and (
                    request.user.is_superuser or session.verdict == session.VERDICT_INCONNU
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
    # Statistiques
    missions_count = missions.count()
    sessions_count = sessions.count()
    context = {
        'map': map_obj,
        'missions': missions,
        'sessions_data': sessions_data,
        'missions_count': missions_count,
        'sessions_count': sessions_count,
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
