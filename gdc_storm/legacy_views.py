from django.views.decorators.csrf import csrf_exempt
from django.db import transaction

from gdc_storm.models import LegacyRole

from django.views.decorators.http import require_POST
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.conf import settings
from django.core.files.storage import default_storage
import os, uuid, tempfile
from gdc_storm.utils import legacy_parse_mission_filename
from gdc_storm.models import Mission, MapName, LegacyMission, Player, LegacyImportError, LegacyGameSession
from gdc_storm.views import format_errors
from yapbol import PBOFile
import re
from django.contrib.auth.models import User, Group
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.contrib.admin.views.decorators import staff_member_required

import logging
from gdc_storm.pbo_extract import is_sqm_binarized, extract_mission_data_from_pbo, extract_briefing_from_pbo
import secrets
import string
import csv
from django.utils.dateparse import parse_datetime

@staff_member_required
def legacy_export(request):
    # Liste des linkedUser distincts (non vides), triés alpha
    linked_users = (LegacyMission.objects.exclude(linkedUser='')
               .values_list('linkedUser', flat=True).distinct())
    linked_users = sorted(set(linked_users), key=lambda x: x.lower())
    selected_user = request.GET.get('linkedUser')
    missions = []
    mission_count = 0
    if selected_user:
        missions = LegacyMission.objects.filter(linkedUser=selected_user).order_by('name')
        mission_count = missions.count()
    return render(request, 'gdc_storm/legacy_export.html', {
        'linked_users': linked_users,
        'selected_user': selected_user,
        'missions': missions,
        'mission_count': mission_count,
    })

@staff_member_required
def bulk_missions(request):
    # Page à compléter pour l'import massif de missions
    return render(request, 'gdc_storm/bulk_missions.html')

@staff_member_required
def bulk_upload_mission(request):
    if request.method != 'POST' or 'pbo_file' not in request.FILES:
        return JsonResponse({'success': False, 'error': 'Aucun fichier .pbo fourni.'}, status=400)
    pbo_file = request.FILES['pbo_file']
    filename = os.path.basename((pbo_file.name or '').replace('\\', '/'))
    if not filename or filename in ('.', '..'):
        return JsonResponse({'success': False, 'error': 'Nom de fichier invalide.'}, status=400)
    temp_dir = os.path.join(tempfile.gettempdir(), 'gdc_storm')
    os.makedirs(temp_dir, exist_ok=True)
    temp_file_name = f"{uuid.uuid4()}_{filename}"
    temp_file_path = os.path.join(temp_dir, temp_file_name)
    with open(temp_file_path, 'wb+') as destination:
        for chunk in pbo_file.chunks():
            destination.write(chunk)
    parsed = legacy_parse_mission_filename(filename)
    if not parsed:
        os.remove(temp_file_path)
        LegacyImportError.objects.create(filename=filename, error_message="Nom de fichier invalide. Format attendu : CPC-TypeDeMission[XX]-Nom_De_La_Mission-VY.nom_de_map.pbo (XX = 2 chiffres)")
        return JsonResponse({'success': False, 'error': "Nom de fichier invalide. Format attendu : CPC-TypeDeMission[XX]-Nom_De_La_Mission-VY.nom_de_map.pbo (XX = 2 chiffres)"}, status=400)
    mission_name, mission_type, max_players, version, map_name = parsed
    MapName.objects.get_or_create(code_name=map_name.lower(), defaults={'display_name': ''})
    # Stockage dans LegacyMission (pas Mission)
    # Extraction PBO (métadonnées, images, etc.)
    errors = []
    try:
        pbo = PBOFile.read_file(temp_file_path)
    except Exception as e:
        os.remove(temp_file_path)
        LegacyImportError.objects.create(filename=filename, error_message=f"Erreur lors de la lecture du fichier .pbo : {e}")
        return JsonResponse({'success': False, 'error': f"Erreur lors de la lecture du fichier .pbo : {e}"}, status=400)
    #is_binarized = is_sqm_binarized(pbo)
    #if is_binarized:
    #    os.remove(temp_file_path)
    #    return JsonResponse({'success': False, 'error': "Le fichier mission.sqm est binarisé. Merci de sauvegarder la mission en mode texte dans l'éditeur avant de l'uploader."}, status=400)
    #try:
    #    sqm_file = pbo['mission.sqm']
    #    sqm_content = sqm_file.data.decode('utf-8', errors='replace')
    #    hc_regex = r'name\s*=\s*"HC_Slot";\s*isPlayable\s*=\s*1;[^}]*type\s*=\s*"HeadlessClient_F";'
    #    if not re.search(hc_regex, sqm_content, re.DOTALL):
    #        errors.append("Erreur : la mission ne contient pas de slot Headless Client correctement configuré. Il doit exister un slot avec name=\"HC_Slot\"; isPlayable=1; type=\"HeadlessClient_F\" dans mission.sqm.")
    #except KeyError:
    #    errors.append("Erreur : mission.sqm introuvable dans le pbo.")
    #except Exception as e:
    #    errors.append(f"Erreur lors du contrôle Headless Client : {e}")
    data, extraction_problems = extract_mission_data_from_pbo(pbo)
    if extraction_problems:
        errors.append("Problèmes détectés lors de l'extraction des métadonnées :<ul>" + ''.join(f"<li>{prob}</li>" for prob in extraction_problems) + "</ul>")
    try:
        briefing, briefing_images = extract_briefing_from_pbo(pbo)
        if briefing is None:
            errors.append("Erreur lors de l'extraction du briefing : briefing non trouvé ou invalide.")
    except Exception as e:
        errors.append(f"Erreur lors de l'extraction du briefing : {e}")
    if errors:
        os.remove(temp_file_path)
        LegacyImportError.objects.create(filename=filename, error_message=format_errors(errors))
        return JsonResponse({'success': False, 'error': format_errors(errors)}, status=400)
    # Sauvegarde du fichier PBO dans legacy_missions/
    file_path = default_storage.save(
        os.path.join('legacy_missions', filename), open(temp_file_path, 'rb'))
    loadscreen_file = None
    if data['loadScreen']:
        try:
            ext = os.path.splitext(data['loadScreen'])[1].lower()
            if ext in ['.jpg', '.jpeg', '.png']:
                img_entry = pbo[data['loadScreen']]
                img_data = img_entry.data
                img_filename = os.path.join('legacy_missions', f"{uuid.uuid4()}{ext}")
                os.makedirs(os.path.join(default_storage.location, 'legacy_missions'), exist_ok=True)
                with default_storage.open(img_filename, 'wb') as imgfile:
                    imgfile.write(img_data)
                loadscreen_file = img_filename
        except Exception as e:
            logging.warning(f"Erreur lors de l'extraction de l'image loadScreen : {e}")
            loadscreen_file = None
    legacy_mission = LegacyMission.objects.create(
        name=mission_name,
        user=request.user,
        authors=data.get('author', ''),
        min_players=int(data['minPlayers']) if data['minPlayers'] else None,
        max_players=int(max_players),
        type=mission_type.upper(),
        pbo_file=file_path,
        version=version.lstrip('Vv'),
        map=map_name.lower(),
        onLoadMission=data.get('onLoadMission', ''),
        overviewText=data.get('overviewText', ''),
        loadScreen=loadscreen_file,
        briefing=briefing,
        briefing_images=briefing_images,
        status='',
    )
    try:
        os.remove(temp_file_path)
    except Exception as e:
        logging.warning(f"Erreur lors de la suppression du fichier temporaire {temp_file_path}: {e}")
    return JsonResponse({'success': True, 'message': f"Mission '{filename}' stockée en temporaire (LegacyMission).", 'legacy_mission_id': legacy_mission.id})

@require_POST
@staff_member_required
def update_linked_user(request):
    import html
    mission_id = request.POST.get('mission_id')
    new_user = request.POST.get('new_user')
    old_user = request.POST.get('old_user')
    # Décodage HTML pour corriger les caractères encodés (&amp; → & etc)
    if new_user:
        new_user = html.unescape(new_user)
    if old_user:
        old_user = html.unescape(old_user)
    if mission_id and new_user:
        try:
            mission = LegacyMission.objects.get(id=mission_id)
            mission.linkedUser = new_user
            mission.save()
            return JsonResponse({'success': True, 'message': f"linkedUser de la mission '{mission.name}' mis à jour en '{new_user}'."})
        except LegacyMission.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Mission introuvable.'}, status=400)
    elif old_user and new_user:
        LegacyMission.objects.filter(linkedUser=old_user).update(linkedUser=new_user)
        return JsonResponse({'success': True, 'message': f"linkedUser modifié en '{new_user}'."})
    else:
        return JsonResponse({'success': False, 'error': 'Paramètres manquants.'}, status=400)

@require_POST
@staff_member_required
def create_user_from_linkeduser(request):
    linked_user = request.POST.get('linkedUser')
    if not linked_user:
        return JsonResponse({'success': False, 'error': 'linkedUser manquant.'}, status=400)
    if User.objects.filter(username=linked_user).exists():
        return JsonResponse({'success': False, 'error': 'Utilisateur déjà existant.'}, status=400)
    # Génère un mot de passe complexe
    alphabet = string.ascii_letters + string.digits + string.punctuation
    password = ''.join(secrets.choice(alphabet) for _ in range(16))
    user = User.objects.create_user(username=linked_user, password=password)
    # Ajoute au groupe Mission Maker
    group, _ = Group.objects.get_or_create(name='Mission Maker')
    user.groups.add(group)
    # Recherche d'un Player existant
    player = Player.objects.filter(name=linked_user).first()
    if player:
        player.users.add(user)
    return JsonResponse({'success': True, 'message': f"Utilisateur '{linked_user}' créé avec succès." + (f" Player associé." if player else " Aucun Player associé."), 'password': password})

@require_POST
@staff_member_required
def export_legacy_missions_to_main(request):
    linked_user = request.POST.get('linkedUser')
    if not linked_user:
        return JsonResponse({'success': False, 'error': 'linkedUser manquant.'}, status=400)
    try:
        user = User.objects.get(username=linked_user)
    except User.DoesNotExist:
        return JsonResponse({'success': False, 'error': "Utilisateur lié introuvable."}, status=400)
    missions = LegacyMission.objects.filter(linkedUser=linked_user)
    count = 0
    errors = []
    for legacy in missions:
        try:
            # Empêche les doublons dans Mission
            if Mission.objects.filter(name=legacy.name, map=legacy.map).exists():
                errors.append(f"Mission déjà existante : {legacy.name} ({legacy.map})")
                continue
                # Crée l'entrée MapName si nécessaire (comme upload_mission)
                #from gdc_storm.models import MapName
                #MapName.objects.get_or_create(code_name=legacy.map, defaults={'display_name': ''})
            mission = Mission.objects.create(
                name=legacy.name,
                user=user,
                authors=legacy.authors,
                min_players=legacy.min_players,
                max_players=legacy.max_players,
                type=legacy.type,
                version=legacy.version,
                map=legacy.map,
                onLoadMission=legacy.onLoadMission,
                overviewText=legacy.overviewText,
                loadScreen=legacy.loadScreen,
                briefing=legacy.briefing,
                briefing_images=legacy.briefing_images,
                status=Mission.STATUS_INCONNU,
            )
            legacy.pbo_file.delete()
            legacy.delete()
            count += 1
        except Exception as e:
            errors.append(f"Erreur sur {legacy.name} ({legacy.map}, v{legacy.version}) : {e}")
    msg = f"{count} mission(s) exportée(s) vers la DB principale."
    if errors:
        msg += "<br>Erreurs :<ul>" + ''.join(f"<li>{err}</li>" for err in errors) + "</ul>"
    return JsonResponse({'success': count > 0, 'message': msg, 'errors': errors})

@staff_member_required
def get_legacy_import_errors(request):
    errors = LegacyImportError.objects.order_by('-created_at')
    return JsonResponse({
        'errors': [
            {'id': e.id, 'filename': e.filename, 'error_message': e.error_message, 'created_at': e.created_at.strftime('%d/%m/%Y %H:%M')} for e in errors
        ]
    })

@require_POST
@staff_member_required
def delete_legacy_import_error(request):
    error_id = request.POST.get('error_id')
    try:
        err = LegacyImportError.objects.get(id=error_id)
        err.delete()
        return JsonResponse({'success': True})
    except LegacyImportError.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Erreur introuvable.'}, status=400)

@require_POST
@staff_member_required
@csrf_exempt
def import_players_csv(request):
    if 'csv_file' not in request.FILES:
        return JsonResponse({'success': False, 'error': 'Aucun fichier CSV fourni.'}, status=400)
    csv_file = request.FILES['csv_file']
    decoded = csv_file.read().decode('utf-8').splitlines()
    reader = csv.DictReader(decoded)
    created = 0
    errors = []
    from gdc_storm.models import LegacyPlayers
    for row in reader:
        pseudo = row.get('PSEUDO')
        date_creation = row.get('DATE_CREATION')
        legacy_id = row.get('ID')
        if not pseudo:
            errors.append(f"Pseudo manquant sur la ligne {row}")
            continue
        try:
            player = Player(name=pseudo)
            if date_creation:
                dt = parse_datetime(date_creation)
                if dt:
                    player.created_at = dt
            player.save()
            # Ajout dans LegacyPlayers
            LegacyPlayers.objects.create(
                legacy_id=int(legacy_id) if legacy_id and legacy_id.isdigit() else None,
                name=pseudo,
                created_at=player.created_at,
                raw_data=row
            )
            created += 1
        except Exception as e:
            errors.append(f"Erreur sur {pseudo}: {e}")
    msg = f"{created} joueur(s) importé(s)."
    if errors:
        msg += "<br>Erreurs :<ul>" + ''.join(f"<li>{err}</li>" for err in errors) + "</ul>"
    return JsonResponse({'success': created > 0, 'message': msg, 'errors': errors})

@require_POST
@staff_member_required
def clear_legacy_missions(request):
    count = LegacyMission.objects.count()
    LegacyMission.objects.all().delete()
    return JsonResponse({'success': True, 'message': f"{count} missions Legacy supprimées."})

@require_POST
@staff_member_required
def clear_legacy_dbs(request):
    count_missions = LegacyMission.objects.count()
    count_roles = LegacyRole.objects.count()
    count_sessions = LegacyGameSession.objects.count()
    LegacyMission.objects.all().delete()
    LegacyRole.objects.all().delete()
    LegacyImportError.objects.all().delete()
    LegacyGameSession.objects.all().delete()
    msg = f"{count_missions} missions Legacy, {count_roles} rôles Legacy, {count_sessions} 'missions sessions' supprimés."
    return JsonResponse({'success': True, 'message': msg})

@require_POST
@staff_member_required
def import_roles_csv(request):
    import csv
    from gdc_storm.models import LegacyRole
    errors = []
    created = 0
    if 'csv_file' not in request.FILES:
        return JsonResponse({'success': False, 'error': 'Aucun fichier CSV fourni.'}, status=400)
    csv_file = request.FILES['csv_file']
    try:
        decoded = csv_file.read().decode('utf-8').splitlines()
        reader = csv.DictReader(decoded)
        for row in reader:
            try:
                legacy_id = int(row.get('ID', '').strip()) if row.get('ID') else None
                name = row.get('NOM', '').strip()
                if not legacy_id or not name:
                    errors.append(f"Ligne ignorée : ID ou NOM manquant ({row})")
                    continue
                # Met à jour ou crée le rôle
                obj, created_obj = LegacyRole.objects.update_or_create(
                    legacy_id=legacy_id,
                    defaults={'name': name}
                )
                if created_obj:
                    created += 1
            except Exception as e:
                errors.append(f"Erreur sur la ligne {row}: {e}")
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Erreur de lecture du CSV : {e}'}, status=400)
    msg = f"{created} rôle(s) importé(s)."
    if errors:
        msg += "<br>Erreurs :<ul>" + ''.join(f"<li>{err}</li>" for err in errors) + "</ul>"
    return JsonResponse({'success': created > 0, 'message': msg, 'errors': errors})


# Import CSV pour LegacyGameSession
@require_POST
@staff_member_required
def import_gamesessions_csv(request):
    import csv
    from gdc_storm.models import LegacyGameSession
    errors = []
    created = 0
    if 'csv_file' not in request.FILES:
        return JsonResponse({'success': False, 'error': 'Aucun fichier CSV fourni.'}, status=400)
    csv_file = request.FILES['csv_file']
    try:
        decoded = csv_file.read().decode('utf-8').splitlines()
        reader = csv.DictReader(decoded)
        for row in reader:
            try:
                session_id = int(row.get('ID', '').strip()) if row.get('ID') else None
                name = row.get('NOM', '').strip()
                start_time = row.get('HDEBUT', '').strip()
                end_time = row.get('HFIN', '').strip()
                verdict = row.get('VERDICT', '').strip()
                map_name = row.get('NOM_MAP', '').strip()
                if not session_id or not name or not start_time or not end_time:
                    errors.append(f"Ligne ignorée : données manquantes ({row})")
                    continue
                from django.utils.dateparse import parse_datetime
                import pytz
                paris = pytz.timezone('Europe/Paris')
                start_dt = parse_datetime(start_time)
                end_dt = parse_datetime(end_time)
                if not start_dt or not end_dt:
                    errors.append(f"Ligne ignorée : format date invalide ({row})")
                    continue
                # Si les dates sont naïves, on les considère comme Paris, puis on convertit en UTC
                if start_dt.tzinfo is None:
                    start_dt = paris.localize(start_dt).astimezone(pytz.UTC)
                else:
                    start_dt = start_dt.astimezone(pytz.UTC)
                if end_dt.tzinfo is None:
                    end_dt = paris.localize(end_dt).astimezone(pytz.UTC)
                else:
                    end_dt = end_dt.astimezone(pytz.UTC)
                obj, created_obj = LegacyGameSession.objects.update_or_create(
                    session_id=session_id,
                    defaults={
                        'name': name,
                        'start_time': start_dt,
                        'end_time': end_dt,
                        'verdict': verdict,
                        'map_name': map_name
                    }
                )
                if created_obj:
                    created += 1
            except Exception as e:
                errors.append(f"Erreur sur la ligne {row}: {e}")
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Erreur de lecture du CSV : {e}'}, status=400)
    msg = f"{created} GameSession(s) importée(s)."
    if errors:
        msg += "<br>Erreurs :<ul>" + ''.join(f"<li>{err}</li>" for err in errors) + "</ul>"
    return JsonResponse({'success': created > 0, 'message': msg, 'errors': errors})


# Import CSV pour MapNames et LegacyMapNames
@require_POST
@staff_member_required
def import_mapnames_csv(request):
    import csv
    from gdc_storm.models import MapName, LegacyMapNames
    errors = []
    created_mapnames = 0
    created_legacy = 0
    if 'csv_file' not in request.FILES:
        return JsonResponse({'success': False, 'error': 'Aucun fichier CSV fourni.'}, status=400)
    csv_file = request.FILES['csv_file']
    try:
        decoded = csv_file.read().decode('utf-8').splitlines()
        reader = csv.DictReader(decoded)
        for row in reader:
            try:
                code_name = row.get('worldName', '').strip().lower()
                display_name = row.get('customName', '').strip()
                game_session_names_raw = row.get('gameSessionNames', '').strip()
                # Remplir MapName
                if code_name:
                    _, created = MapName.objects.update_or_create(
                        code_name=code_name,
                        defaults={'display_name': display_name}
                    )
                    if created:
                        created_mapnames += 1
                # Remplir LegacyMapNames
                game_session_names = []
                if game_session_names_raw:
                    # Nettoyage : enlever les guillemets et split sur la virgule
                    game_session_names = [s.strip() for s in game_session_names_raw.replace('"', '').split(',') if s.strip()]
                obj, created_legacy_obj = LegacyMapNames.objects.update_or_create(
                    code_name=code_name,
                    defaults={
                        'display_name': display_name,
                        'game_session_names': game_session_names
                    }
                )
                if created_legacy_obj:
                    created_legacy += 1
            except Exception as e:
                errors.append(f"Erreur sur la ligne {row}: {e}")
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Erreur de lecture du CSV : {e}'}, status=400)
    msg = f"{created_mapnames} MapName(s) et {created_legacy} LegacyMapNames importés."
    if errors:
        msg += "<br>Erreurs :<ul>" + ''.join(f"<li>{err}</li>" for err in errors) + "</ul>"
    return JsonResponse({'success': created_mapnames > 0 or created_legacy > 0, 'message': msg, 'errors': errors})


# Import CSV pour LegacyGameSessionPlayerRole
@require_POST
@staff_member_required
def import_gamesession_player_role_csv(request):
    import csv
    from gdc_storm.models import LegacyGameSessionPlayerRole
    errors = []
    created = 0
    if 'csv_file' not in request.FILES:
        return JsonResponse({'success': False, 'error': 'Aucun fichier CSV fourni.'}, status=400)
    csv_file = request.FILES['csv_file']
    try:
        decoded = csv_file.read().decode('utf-8').splitlines()
        reader = csv.DictReader(decoded)
        for row in reader:
            try:
                data_id = int(row.get('ID', '').strip()) if row.get('ID') else None
                player_id = int(row.get('ID_JOUEUR', '').strip()) if row.get('ID_JOUEUR') else None
                gamesession_id = int(row.get('ID_MISSION', '').strip()) if row.get('ID_MISSION') else None
                role_id = int(row.get('ID_ROLE', '').strip()) if row.get('ID_ROLE') else None
                status = row.get('STATUS', '').strip()
                if not data_id or not player_id or not role_id:
                    errors.append(f"Ligne ignorée : données manquantes ({row})")
                    continue
                obj, created_obj = LegacyGameSessionPlayerRole.objects.update_or_create(
                    data_id=data_id,
                    player_id=player_id,
                    gamesession_id=gamesession_id,
                    role_id=role_id,
                    defaults={'status': status}
                )
                if created_obj:
                    created += 1
            except Exception as e:
                errors.append(f"Erreur sur la ligne {row}: {e}")
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Erreur de lecture du CSV : {e}'}, status=400)
    msg = f"{created} mapping(s) importé(s)."
    if errors:
        msg += "<br>Erreurs :<ul>" + ''.join(f"<li>{err}</li>" for err in errors) + "</ul>"
    return JsonResponse({'success': created > 0, 'message': msg, 'errors': errors})



@require_POST
@staff_member_required
@csrf_exempt
def import_legacy_gamesessions(request):
    from gdc_storm.models import LegacyGameSession, GameSession, Mission, LegacyGameSessionPlayerRole, LegacyPlayers, Player, LegacyRole, GameSessionPlayer, LegacyMapNames
    legacy_sessions = LegacyGameSession.objects.all()
    imported = 0
    errors = []
    for legacy in legacy_sessions:
        version = ''
        mission_name_no_version = legacy.name
        version_match = re.search(r'-[Vv](\d+)$', legacy.name)
        if version_match:
            version = version_match.group(1)
            mission_name_no_version = re.sub(r'-[Vv]\d+$', '', legacy.name)
        
        # Chercher la mission correspondante
        mission = Mission.objects.filter(name__icontains=mission_name_no_version).first()
        verdict_map = {
            'SUCCES': GameSession.VERDICT_SUCCES,
            'ECHEC': GameSession.VERDICT_ECHEC,
            'INCONNU': GameSession.VERDICT_INCONNU,
            '@EFFACER': GameSession.VERDICT_EFFACER,
            '@ EFFACER': GameSession.VERDICT_EFFACER,
            'RELANCEE': GameSession.VERDICT_EFFACER,
            '@TEST': GameSession.VERDICT_EFFACER,
            'PVP': GameSession.VERDICT_PVP,
            'TRAINING': GameSession.VERDICT_TRAINING,
        }
        verdict = verdict_map.get(legacy.verdict.strip().upper())
        if not verdict:
            errors.append(f"Verdict non reconnu pour session {legacy.session_id} : {legacy.verdict}")
            continue
        # Trouver le code_name de la map
        map_matches = []
        for lmap in LegacyMapNames.objects.all():
            gs_names = lmap.game_session_names or []
            if gs_names:
                for gs_name in gs_names:
                    if str(gs_name).strip().lower() == legacy.map_name.strip().lower():
                        map_matches.append(lmap)
            else:
                if lmap.display_name.strip().lower() == legacy.map_name.strip().lower():
                    map_matches.append(lmap)
        if len(map_matches) == 0:
            errors.append(f"Aucune correspondance de map trouvée pour session {legacy.session_id} : {legacy.map_name}")
            continue
        if len(map_matches) > 1:
            errors.append(f"Plusieurs correspondances de map trouvées pour session {legacy.session_id} : {legacy.map_name}")
            continue
        code_name = map_matches[0].code_name

        try:
            with transaction.atomic():
                gs = GameSession.objects.create(
                    mission=mission,
                    name=mission_name_no_version,
                    map=code_name,
                    version=version,
                    start_time=legacy.start_time,
                    end_time=legacy.end_time,
                    verdict=verdict
                )
                # Import des joueurs/roles
                legacy_gsprs = LegacyGameSessionPlayerRole.objects.filter(gamesession_id=legacy.session_id)
                for gspr in legacy_gsprs:
                    legacy_player = LegacyPlayers.objects.filter(legacy_id=gspr.player_id).first()
                    player_obj = None
                    if legacy_player:
                        player_obj = Player.objects.filter(name=legacy_player.name).first()
                    role_name = None
                    legacy_role = LegacyRole.objects.filter(legacy_id=gspr.role_id).first()
                    if legacy_role:
                        role_name = legacy_role.name
                    if player_obj and role_name:
                        status_map = {
                            'Mort': 'MORT',
                            'mort': 'MORT',
                            'Vivant': 'VIVANT',
                            'vivant': 'VIVANT',
                        }
                        status_value = status_map.get(str(gspr.status).strip(), 'VIVANT')
                        GameSessionPlayer.objects.create(
                            session=gs,
                            player=player_obj,
                            role=role_name,
                            status=status_value
                        )
                    else:
                        errors.append(f"Joueur ou rôle introuvable pour session {legacy.session_id} (player_id={gspr.player_id}, role_id={gspr.role_id})")
                legacy.delete()
                imported += 1
        except Exception as e:
            errors.append(f"Erreur import session {legacy.session_id} : {e}")
    msg = f"{imported} GameSession(s) importée(s) depuis Legacy."
    if errors:
        msg += "<br>Erreurs :<ul>" + ''.join(f"<li>{err}</li>" for err in errors) + "</ul>"
    return JsonResponse({'success': imported > 0, 'message': msg, 'errors': errors})
