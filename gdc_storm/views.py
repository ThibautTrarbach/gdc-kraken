from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password
# Changement de mot de passe utilisateur
@login_required
def change_password(request):
    if request.method == 'POST':
        old_password = request.POST.get('old_password')
        new_password1 = request.POST.get('new_password1')
        new_password2 = request.POST.get('new_password2')
        user = request.user
        if not user.check_password(old_password):
            messages.error(request, "Ancien mot de passe incorrect.")
        elif new_password1 != new_password2:
            messages.error(request, "Les nouveaux mots de passe ne correspondent pas.")
        elif not new_password1 or len(new_password1) < 6:
            messages.error(request, "Le nouveau mot de passe doit contenir au moins 6 caractères.")
        else:
            user.set_password(new_password1)
            user.save()
            update_session_auth_hash(request, user)  # Reste connecté
            messages.success(request, "Mot de passe modifié avec succès.")
            return redirect('home')
    return render(request, 'gdc_storm/change_password.html')

import re
import uuid
import os
import logging
import glob
import tempfile
import datetime
from collections import defaultdict
import secrets

from django.core.cache import cache
from django.db.models import Count, Max
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.core.files.storage import default_storage
from django.views.decorators.http import require_POST, require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.contrib.auth.models import Group, User
from django.conf import settings
from django.utils.dateparse import parse_datetime
from functools import wraps

from yapbol import PBOFile

from .models import Mission, MapName, Player, GameSession, GameSessionPlayer, ApiToken
from .models import LegacyRole, LegacyMission, LegacyImportError, LegacyGameSession, LegacyMapNames, LegacyGameSessionPlayerRole, LegacyPlayers
from .forms import MissionStatusForm
from gdc_storm.utils import parse_mission_filename
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
    now = int(os.path.getmtime(temp_dir)) if os.path.exists(temp_dir) else 0
    for temp_file in glob.glob(os.path.join(temp_dir, '*')):
        try:
            if os.path.isfile(temp_file):
                age = int(os.path.getmtime(temp_file))
                if now - age > max_age_seconds:
                    os.remove(temp_file)
        except Exception as e:
            logging.warning(f"Erreur lors du nettoyage du fichier temporaire {temp_file}: {e}")

# Upload mission view
@login_required
def upload_mission(request):
    error_message = None
    show_confirm = False
    show_update_confirm = False
    duplicate_missions = []
    temp_file_path = None
    temp_file_name = None
    temp_dir = os.path.join(tempfile.gettempdir(), 'gdc_storm')
    os.makedirs(temp_dir, exist_ok=True)
    # Nettoyage automatique des fichiers temporaires orphelins (plus d'1h)
    clean_temp_files(temp_dir)
    if not user_is_mission_maker(request.user):
        return HttpResponse("Vous n'avez pas le droit de publier une mission.", status=403)
    if request.method == 'POST':
        # Toujours sauvegarder le fichier en temporaire AVANT tout check
        if request.FILES.get('pbo_file'):
            pbo_file = request.FILES['pbo_file']
            filename = pbo_file.name
            temp_file_name = f"{uuid.uuid4()}_{filename}"
            temp_file_path = os.path.join(temp_dir, temp_file_name)
            with open(temp_file_path, 'wb+') as destination:
                for chunk in pbo_file.chunks():
                    destination.write(chunk)
        elif request.POST.get('temp_file_path'):
            temp_file_path = request.POST['temp_file_path']
            temp_file_name = request.POST.get('temp_file_name')
            filename = temp_file_name.split('_', 1)[-1] if temp_file_name and '_' in temp_file_name else temp_file_name
        # Cas confirmation : on récupère le chemin du fichier temporaire
        if request.POST.get('confirm_publish') and temp_file_path:
            if not os.path.exists(temp_file_path):
                error_message = "Le fichier temporaire n'existe plus. Merci de recommencer l'upload."
            else:
                original_filename = temp_file_name.split('_', 1)[-1] if '_' in temp_file_name else temp_file_name
                parsed = parse_mission_filename(original_filename)
                if not parsed:
                    error_message = "Nom de fichier invalide."
                else:
                    mission_name, mission_type, max_players, version, map_name = parsed
                    map_name = map_name.lower()
                    MapName.objects.get_or_create(code_name=map_name, defaults={'display_name': ''})
                    mission, error_message = create_mission_from_pbo(
                        request, temp_file_path, original_filename, mission_name, mission_type, max_players, version, map_name
                    )
                    if not error_message:
                        return redirect(reverse('mission_detail', args=[mission.id]) + '?success=1')
        # Confirmation de mise à jour : NE PAS exiger request.FILES['pbo_file'] si fichier temporaire
        elif request.POST.get('confirm_update'):
            if 'temp_file_name' in request.POST and 'temp_file_path' in request.POST and os.path.exists(request.POST['temp_file_path']):
                temp_file_name = request.POST['temp_file_name']
                temp_file_path = request.POST['temp_file_path']
                file_name_to_parse = temp_file_name.split('_', 1)[-1] if '_' in temp_file_name else temp_file_name
                parsed = parse_mission_filename(file_name_to_parse)
                if not parsed:
                    error_message = "Nom de fichier invalide lors de la confirmation de mise à jour."
                    return render(request, 'gdc_storm/upload_mission.html', {
                        'error_message': error_message
                    })
                mission_name, mission_type, max_players, version, map_name = parsed
                map_name = map_name.lower()
                existing = Mission.objects.filter(name=mission_name, map=map_name, max_players=int(max_players))
                if not existing.exists():
                    error_message = "Mission à mettre à jour introuvable."
                    return render(request, 'gdc_storm/upload_mission.html', {
                        'error_message': error_message
                    })
                existing_mission = existing.first()
                updated_mission, error_message = update_mission_from_pbo(
                    request, existing_mission, temp_file_path, filename, mission_type, max_players, version, map_name
                )
                if error_message:
                    return render(request, 'gdc_storm/upload_mission.html', {
                        'error_message': error_message
                    })
                return redirect(reverse('mission_detail', args=[updated_mission.id]) + '?success=1')
            else:
                error_message = "Fichier temporaire manquant ou expiré lors de la confirmation de mise à jour. Merci de recommencer l'upload."
                return render(request, 'gdc_storm/upload_mission.html', {
                    'error_message': error_message
                })
        elif request.FILES.get('pbo_file'):
            pbo_file = request.FILES['pbo_file']
            filename = pbo_file.name
            parsed = parse_mission_filename(filename)
            if not parsed:
                error_message = "Nom de fichier invalide. Format attendu : CPC-TypeDeMission[XX]-Nom_De_La_Mission-VY.nom_de_map.pbo (XX = 2 chiffres)"
            else:
                mission_name, mission_type, max_players, version, map_name = parsed
                map_name = map_name.lower()
                existing = Mission.objects.filter(name=mission_name, map=map_name, max_players=int(max_players))
                if existing.exists():
                    existing_mission = existing.first()
                    try:
                        existing_version = int(existing_mission.version)
                        new_version = int(version.lstrip('Vv'))
                    except Exception:
                        existing_version = existing_mission.version
                        new_version = version.lstrip('Vv')
                    if str(new_version) > str(existing_version):
                        temp_file_name = f"{uuid.uuid4()}_{filename}"
                        temp_file_path = os.path.join(temp_dir, temp_file_name)
                        with open(temp_file_path, 'wb+') as destination:
                            for chunk in pbo_file.chunks():
                                destination.write(chunk)
                        show_update_confirm = True
                        try:
                            map_obj = MapName.objects.get(code_name=map_name)
                            map_display = map_obj.display_name or map_name
                        except MapName.DoesNotExist:
                            map_display = map_name
                        update_context = {
                            'mission_name': existing_mission.name,
                            'map': map_display,
                            'old_version': existing_mission.version,
                            'new_version': version.lstrip('Vv'),
                            'temp_file_path': temp_file_path,
                            'temp_file_name': temp_file_name,
                            'filename': filename,
                        }
                        return render(request, 'gdc_storm/upload_mission.html', {
                            'show_update_confirm': show_update_confirm,
                            **update_context
                        })
                    else:
                        error_message = f"Une mission avec ce nom, cette carte et ce nombre de joueurs max existe déjà avec une version supérieure ou égale ({existing_mission.version}). (Fichier : {filename})"
                else:
                    duplicates = Mission.objects.filter(name=mission_name)
                    if duplicates.exists() and not request.POST.get('confirm_publish'):
                        show_confirm = True
                        duplicate_missions = []
                        for m in duplicates:
                            try:
                                map_obj = MapName.objects.get(code_name=m.map)
                                map_display = map_obj.display_name or m.map
                            except MapName.DoesNotExist:
                                map_display = m.map
                            duplicate_missions.append({
                                'full_name': f"{m.name}",
                                'map': map_display,
                                'owner': m.user.username if m.user else '—'
                            })
                        temp_file_name = f"{uuid.uuid4()}_{filename}"
                        temp_file_path = os.path.join(temp_dir, temp_file_name)
                        with open(temp_file_path, 'wb+') as destination:
                            for chunk in pbo_file.chunks():
                                destination.write(chunk)
        else:
            error_message = "Aucun fichier .pbo n'a été fourni. Merci de sélectionner un fichier avant de publier."
        if not error_message and not show_confirm and not show_update_confirm:
            MapName.objects.get_or_create(code_name=map_name.lower(), defaults={'display_name': ''})
            try:
                mission, error_message = create_mission_from_pbo(
                    request, temp_file_path, filename, mission_name, mission_type, max_players, version, map_name
                )
            except ValueError as ve:
                error_message = str(ve)
            if not error_message:
                return redirect(reverse('mission_detail', args=[mission.id]) + '?success=1')
    return render(request, 'gdc_storm/upload_mission.html', {
        'error_message': error_message,
        'show_confirm': show_confirm,
        'show_update_confirm': show_update_confirm,
        'duplicate_missions': duplicate_missions,
        'filename': filename if request.method == 'POST' and request.FILES.get('pbo_file') else None,
        'temp_file_path': temp_file_path,
        'temp_file_name': temp_file_name
    })

def get_map_display(map_code):
    """Retourne le display_name d'une carte à partir de son code_name, ou le code si non trouvé."""
    try:
        map_obj = MapName.objects.get(code_name=map_code)
        return map_obj.display_name or map_code
    except MapName.DoesNotExist:
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
        'min': 'max_players',
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
    missions_qs = Mission.objects.all().annotate(
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
    # Cache global pour les noms de map
    MAP_CACHE_KEY = 'map_display_names_v1'
    map_display_cache = cache.get(MAP_CACHE_KEY)
    if not map_display_cache:
        all_maps = MapName.objects.all()
        map_display_cache = {m.code_name: m.display_name or m.code_name for m in all_maps}
        cache.set(MAP_CACHE_KEY, map_display_cache, 3600)  # 1h
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
    sessions = mission.game_sessions.all().order_by('-start_time')
    sessions_data = []
    for session in sessions:
        duration_min = None
        if session.start_time and session.end_time:
            duration_min = int((session.end_time - session.start_time).total_seconds() // 60)
        players_count = session.players.count()
        vivant_count = session.players.filter(status='VIVANT').count()
        sessions_data.append({
            'id': session.id,
            'name': session.name,
            'start_time': session.start_time,
            'duration_min': duration_min,
            'verdict': session.verdict,
            'verdict_display': session.get_verdict_display(),
            'players_count': players_count,
            'vivant_count': vivant_count,
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
    oldest_player = user_profile.players.order_by('created_at').first()
    oldest_player_created = oldest_player.created_at if oldest_player else None
    player_ids = list(user_profile.players.values_list('id', flat=True))
    gamesession_players = GameSessionPlayer.objects.filter(player_id__in=player_ids).select_related('session').order_by('-session__start_time')
    sessions_played = [gsp.session for gsp in gamesession_players]
    total_sessions_played = len(set(sessions_played))
    total_missions_published = len(missions)
    session_ids = list(set([s.id for s in sessions_played]))
    sessions = GameSession.objects.filter(id__in=session_ids).select_related('mission')
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
        players_count = s.players.count()
        vivant_count = s.players.filter(status='VIVANT').count()
        duration_min = None
        if s.start_time and s.end_time:
            duration_min = int((s.end_time - s.start_time).total_seconds() // 60)
        sessions_data.append({
            'session': s,
            'players_count': players_count,
            'vivant_count': vivant_count,
            'duration_min': duration_min,
        })
    # Filtres sessions
    if filter_nom:
        sessions_data = [d for d in sessions_data if filter_nom in (d['session'].name or '').lower()]
    if filter_carte:
        sessions_data = [d for d in sessions_data if filter_carte in (get_map_display(d['session'].map) or '').lower()]
    if filter_verdict:
        sessions_data = [d for d in sessions_data if filter_verdict in (d['session'].get_verdict_display or '').lower()]
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
    context = {
        'user_profile': user_profile,
        'user_role': get_user_role(user_profile),
        'missions': missions,
        'map_displays': map_displays,
        'full_names': full_names,
        'sessions_played': sessions_played,
        'oldest_player_created': oldest_player_created,
        'total_sessions_played': total_sessions_played,
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

def create_mission_from_pbo(request, temp_file_path, filename, mission_name, mission_type, max_players, version, map_name, error_message=None):
    errors = []
    try:
        pbo = PBOFile.read_file(temp_file_path)
    except Exception as e:
        errors.append(f"Erreur lors de la lecture du fichier .pbo : {e}")
        return None, format_errors(errors)
    is_binarized = is_sqm_binarized(pbo)
    if is_binarized:
        errors.append("Le fichier mission.sqm est binarisé. Merci de sauvegarder la mission en mode texte dans l'éditeur avant de l'uploader.")
    # --- Contrôle Headless Client ---
    try:
        sqm_file = pbo['mission.sqm']
        sqm_content = sqm_file.data.decode('utf-8', errors='replace')
        hc_regex = r'name\s*=\s*"HC_Slot";\s*isPlayable\s*=\s*1;[^}]*type\s*=\s*"HeadlessClient_F";'
        if not re.search(hc_regex, sqm_content, re.DOTALL):
            errors.append("Erreur : la mission ne contient pas de slot Headless Client correctement configuré. Il doit exister un slot avec name=\"HC_Slot\"; isPlayable=1; type=\"HeadlessClient_F\" dans mission.sqm.")
    except KeyError:
        errors.append("Erreur : mission.sqm introuvable dans le pbo.")
    except Exception as e:
        errors.append(f"Erreur lors du contrôle Headless Client : {e}")
    data, extraction_problems = extract_mission_data_from_pbo(pbo)
    if extraction_problems:
        errors.append("Problèmes détectés lors de l'extraction des métadonnées :<ul>" + ''.join(f"<li>{prob}</li>" for prob in extraction_problems) + "</ul>")
    # Extraction du briefing et des images de briefing
    try:
        briefing, briefing_images = extract_briefing_from_pbo(pbo)
        if briefing is None:
            errors.append("Erreur lors de l'extraction du briefing : briefing non trouvé ou invalide.")
    except Exception as e:
        errors.append(f"Erreur lors de l'extraction du briefing : {e}")
    if errors:
        return None, format_errors(errors)
    loadscreen_file = None
    if data['loadScreen']:
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
    mission = Mission.objects.create(
        name=mission_name,
        user=request.user,
        authors=data['author'] or '',
        min_players=int(data['minPlayers']) if data['minPlayers'] else None,
        max_players=int(max_players),
        type=mission_type.upper(),
        version=version.lstrip('Vv'),
        map=map_name.lower(),
        onLoadMission=data['onLoadMission'] or Mission.DEFAULT_NOT_PROVIDED,
        overviewText=data['overviewText'] or Mission.DEFAULT_NOT_PROVIDED,
        loadScreen=loadscreen_file,
        briefing=briefing,
    )
    # Stocke la liste des images de briefing pour suppression ultérieure
    if briefing_images:
        mission.briefing_images = briefing_images
        mission.save(update_fields=['briefing_images'])
    try:
        os.rename(temp_file_path, os.path.join(settings.MISSIONS_PBO_STORAGE_PATH, filename))
    except Exception as e:
        return mission, f"Mission créée, mais erreur lors de la sauvegarde du PBO: {e}"
    return mission, None

def format_errors(errors):
    if not errors:
        return None
    return '<br/>'.join(errors)

def update_mission_from_pbo(request, existing_mission, temp_file_path, filename, mission_type, max_players, version, map_name):
    is_admin = request.user.is_superuser
    is_owner = existing_mission.user == request.user
    if not (is_admin or is_owner):
        return None, "Vous n'avez pas le droit de mettre à jour cette mission (seul le propriétaire ou un admin peut le faire)."
    if not os.path.exists(temp_file_path):
        return None, "Fichier temporaire manquant ou expiré lors de la confirmation de mise à jour. Merci de recommencer l'upload."
    try:
        pbo = PBOFile.read_file(temp_file_path)
    except Exception as e:
        return None, f"Erreur lors de la lecture du fichier .pbo : {e}"
    is_binarized = is_sqm_binarized(pbo)
    if is_binarized:
        return None, "Le fichier mission.sqm est binarisé. Merci de sauvegarder la mission en mode texte dans l'éditeur avant de l'uploader."
    data, extraction_problems = extract_mission_data_from_pbo(pbo)
    if extraction_problems:
        error_message = "Problèmes détectés lors de l'extraction des métadonnées :"
        error_message += "<ul>"
        for prob in extraction_problems:
            error_message += f"<li>{prob}</li>"
        error_message += "</ul>"
        return None, error_message
    # Extraction du briefing et des images de briefing
    try:
        briefing, briefing_images = extract_briefing_from_pbo(pbo)
    except Exception as e:
        return None, f"Erreur lors de l'extraction du briefing : {e}"

    if not is_admin:
        existing_mission.user = request.user
    existing_mission.authors = data['author'] or ''
    existing_mission.min_players = int(data['minPlayers']) if data['minPlayers'] else None
    existing_mission.max_players = int(max_players)
    existing_mission.type = mission_type.upper()
    existing_mission.version = version.lstrip('Vv')
    existing_mission.map = map_name.lower()
    existing_mission.onLoadMission = data['onLoadMission'] or Mission.DEFAULT_NOT_PROVIDED
    existing_mission.overviewText = data['overviewText'] or Mission.DEFAULT_NOT_PROVIDED
    existing_mission.briefing = briefing
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
    if data['loadScreen']:
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
    existing_mission.save()
    try:
        os.rename(temp_file_path, os.path.join(settings.MISSIONS_PBO_STORAGE_PATH, filename))
    except Exception as e:
        return existing_mission, f"Mission mise à jour, mais erreur lors de la sauvegarde du PBO: {e}"
    return existing_mission, None

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
    user = player.users.first() if player.users.count() > 0 else None
    if user:
        return redirect(reverse('user_profile', args=[user.id]))
    # Tri dynamique
    sort = request.GET.get('sort', 'date')
    order = request.GET.get('order', 'desc')
    gamesession_players = GameSessionPlayer.objects.filter(player=player).select_related('session').order_by('-session__start_time')
    sessions_played = [gsp.session for gsp in gamesession_players]
    total_sessions_played = len(set(sessions_played))
    session_ids = list(set([s.id for s in sessions_played]))
    sessions = GameSession.objects.filter(id__in=session_ids).select_related('mission')
    # get_map_display est importé en début de fichier
    all_map_displays = {}
    for s in sessions:
        if s.mission:
            all_map_displays[s.mission.id] = get_map_display(s.mission.map)
        else:
            all_map_displays[s.id] = get_map_display(s.map)
    sessions_data = []
    for s in sessions:
        players_count = s.players.count()
        vivant_count = s.players.filter(status='VIVANT').count()
        duration_min = None
        if s.start_time and s.end_time:
            duration_min = int((s.end_time - s.start_time).total_seconds() // 60)
        sessions_data.append({
            'session': s,
            'players_count': players_count,
            'vivant_count': vivant_count,
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
    return render(request, 'gdc_storm/player_detail.html', {
        'player': player,
        'sessions_played': sessions_played,
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
    players = Player.objects.all().order_by('name')
    if request.method == 'POST':
        selected_ids = request.POST.getlist('players')
        # Ne permettre de lier que les Players non liés ou déjà liés à l'utilisateur
        allowed_ids = [str(p.id) for p in players if p.users.count() == 0 or user in p.users.all()]
        filtered_ids = [int(pid) for pid in selected_ids if pid in allowed_ids]
        user.players.set(filtered_ids)
        user.save()
        return render(request, 'gdc_storm/player_mapping.html', {'players': players, 'success': True, 'selected_ids': filtered_ids})
    selected_ids = list(user.players.values_list('id', flat=True))
    return render(request, 'gdc_storm/player_mapping.html', {'players': players, 'selected_ids': selected_ids})

def session_list(request):
    # defaultdict est importé en début de fichier
    CACHE_KEY = 'session_list_data_v1'
    cache_data = cache.get(CACHE_KEY)
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
        cache.set(CACHE_KEY, {'sessions': sessions, 'gsp_by_session': gsp_by_session}, 300)  # 5 min

    sort_fields = {
        'nom': 'name',
        'carte': 'map',
        'date': 'start_time',
        'duration': 'end_time',
        'verdict': 'verdict',
    }
    sort_field = sort_fields.get(sort, 'start_time')
    if sort == 'duration':
        sessions = list(sessions)
        sessions.sort(key=lambda s: (s.end_time is not None, (s.end_time-s.start_time).total_seconds() if s.end_time else 0), reverse=(order=='desc'))
    else:
        if order == 'desc':
            sort_field = '-' + sort_field
        sessions = sorted(sessions, key=lambda s: getattr(s, sort_fields.get(sort, 'start_time')), reverse=(order=='desc'))

    # Cache global pour les noms de map
    MAP_CACHE_KEY = 'map_display_names_v1'
    map_display_cache = cache.get(MAP_CACHE_KEY)
    if not map_display_cache:
        # Récupère tous les MapName en une requête
        all_maps = MapName.objects.all()
        map_display_cache = {m.code_name: m.display_name or m.code_name for m in all_maps}
        cache.set(MAP_CACHE_KEY, map_display_cache, 3600)  # 1h
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
            messages.success(request, f"Statut de {updated} joueur(s) mis à jour.")
        else:
            messages.info(request, "Aucun changement de statut détecté.")
    missions_candidates = []
    show_associate_btn = False
    user_can_edit_verdict = False
    # Gestion du POST pour le verdict
    if request.method == 'POST' and 'set_verdict' in request.POST:
        verdict = request.POST.get('verdict')
        if verdict in dict(GameSession.VERDICT_CHOICES):
            session.verdict = verdict
            session.save()
            messages.success(request, "Verdict mis à jour.")
            # Afficher le message sur la même page sans redirection
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
        import re
        # Enlève la version et le préfixe CPC-XX[YY]-
        name_no_version = re.sub(r'-[Vv]\d+$', '', session.name)
        name_clean = re.sub(r'^CPC-\w+\[\d+\]-', '', name_no_version)
        all_missions = Mission.objects.filter(map=session.map)
        missions_candidates = [
            m for m in all_missions
            if re.sub(r'^CPC-\w+\[\d+\]-', '', m.name) == name_clean
        ]
        show_associate_btn = request.user.is_authenticated and request.user.is_superuser
        no_mission_found = len(missions_candidates) == 0
        if request.method == 'POST' and 'mission_id' in request.POST:
            mission_id = request.POST.get('mission_id')
            try:
                mission = Mission.objects.get(id=mission_id)
                session.mission = mission
                session.save()
                messages.success(request, "Mission associée avec succès à la session.")
                return redirect('session_detail', session_id=session.id)
            except Mission.DoesNotExist:
                messages.error(request, "Mission introuvable.")
    # Correction : calculer user_can_edit_verdict pour tous les cas, et le passer au template
    user_can_edit_verdict = False
    if request.user.is_authenticated:
        if request.user.is_superuser:
            user_can_edit_verdict = True
        elif session.verdict == session.VERDICT_INCONNU:
            user_can_edit_verdict = True
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
    sessions = GameSession.objects.filter(mission__isnull=True).order_by('-start_time')
    sessions_data = []
    for s in sessions:
        duration_min = None
        if s.start_time and s.end_time:
            duration_min = int((s.end_time - s.start_time).total_seconds() // 60)
        players_count = s.players.count()
        vivant_count = s.players.filter(status='VIVANT').count()
        sessions_data.append({
            'id': s.id,
            'name': s.name,
            'map': s.map,
            'start_time': s.start_time,
            'duration_min': duration_min,
            'verdict': s.verdict,
            'get_verdict_display': s.get_verdict_display(),
            'players_count': players_count,
            'vivant_count': vivant_count,
        })
    return render(request, 'gdc_storm/orphan_sessions.html', {'sessions': sessions_data})

def map_detail(request, map_id):
    map_obj = get_object_or_404(MapName, id=map_id)
    # Missions dont le champ map == code_name de la carte
    missions = Mission.objects.filter(map=map_obj.code_name).order_by('-id')
    # Sessions dont le champ map == code_name de la carte
    sessions = GameSession.objects.filter(map=map_obj.code_name).order_by('-start_time')
    sessions_data = []
    for session in sessions:
        duration_min = None
        if session.start_time and session.end_time:
            duration_min = int((session.end_time - session.start_time).total_seconds() // 60)
        players_count = session.players.count()
        vivant_count = session.players.filter(status='VIVANT').count()
        sessions_data.append({
            'id': session.id,
            'name': session.name,
            'start_time': session.start_time,
            'duration_min': duration_min,
            'verdict': session.verdict,
            'verdict_display': session.get_verdict_display(),
            'players_count': players_count,
            'vivant_count': vivant_count,
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
    maps = MapName.objects.all()
    map_data = []
    for m in maps:
        missions_count = Mission.objects.filter(map=m.code_name).count()
        sessions_count = GameSession.objects.filter(map=m.code_name).count()
        map_data.append({
            'id': m.id,
            'display_name': m.display_name,
            'code_name': m.code_name,
            'missions_count': missions_count,
            'sessions_count': sessions_count,
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
