import datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse
from .models import GameSession, GameSessionPlayer, Player
from functools import wraps
from .models import ApiToken
from .utils import find_mission_for_session

def require_api_token(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        token = request.headers.get('Authorization') or request.GET.get('api_token')
        if not token:
            return JsonResponse({'success': False, 'error': 'Token manquant.'}, status=401)
        try:
            api_token = ApiToken.objects.get(key=token, is_active=True)
        except ApiToken.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Token invalide.'}, status=403)
        return view_func(request, *args, **kwargs)
    return _wrapped_view

@csrf_exempt
@require_http_methods(["POST"])
@require_api_token
def api_create_gamesession(request):
    import json
    data = json.loads(request.body.decode())
    mission_name = data.get('mission_name')
    map_name = data.get('map')
    start_time = data.get('start_time')
    mission, mission_name_no_version, version, map_normalized = find_mission_for_session(
        mission_name, map_name
    )
    try:
        start_dt = datetime.datetime.fromtimestamp(float(start_time))
    except Exception:
        return JsonResponse({'success': False, 'error': 'start_time invalide'}, status=400)
    session = GameSession.objects.create(
        mission=mission,
        name=mission_name_no_version,
        map=map_normalized,
        version=version,
        start_time=start_dt
    )
    return JsonResponse({'success': True, 'session_id': session.id, 'mission_found': bool(mission)})

@csrf_exempt
@require_http_methods(["POST"])
@require_api_token
def api_update_gamesession_end(request, session_id):
    import json
    data = json.loads(request.body.decode())
    end_time = data.get('end_time')
    try:
        end_dt = datetime.datetime.fromtimestamp(float(end_time))
    except Exception:
        return JsonResponse({'success': False, 'error': 'end_time invalide'}, status=400)
    try:
        session = GameSession.objects.get(id=session_id)
        session.end_time = end_dt
        session.save()
        return JsonResponse({'success': True})
    except GameSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'GameSession introuvable'}, status=404)

@csrf_exempt
@require_http_methods(["POST"])
@require_api_token
def api_add_gamesession_player(request, session_id):
    import json
    data = json.loads(request.body.decode())
    player_name = data.get('player_name')
    role = data.get('role')
    if not player_name or not role:
        return JsonResponse({'success': False, 'error': 'player_name et role requis'}, status=400)
    try:
        session = GameSession.objects.get(id=session_id)
    except GameSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'GameSession introuvable'}, status=404)
    player_obj, _ = Player.objects.get_or_create(name=player_name)
    gsp = GameSessionPlayer.objects.create(session=session, player=player_obj, role=role)
    return JsonResponse({'success': True, 'player_id': gsp.id, 'player_db_id': player_obj.id})

@csrf_exempt
@require_http_methods(["POST"])
@require_api_token
def api_update_gamesession_player_status(request, session_id):
    import json
    data = json.loads(request.body.decode())
    player_name = data.get('player_name')
    status = data.get('status')
    if not player_name or not status:
        return JsonResponse({'success': False, 'error': 'player_name et status requis'}, status=400)
    if status not in dict(GameSessionPlayer.STATUS_CHOICES):
        return JsonResponse({'success': False, 'error': 'Status invalide'}, status=400)
    try:
        player = Player.objects.get(name=player_name)
    except Player.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Player introuvable'}, status=404)
    try:
        gsp = GameSessionPlayer.objects.get(player=player, session_id=session_id)
        gsp.status = status
        gsp.save()
        return JsonResponse({'success': True})
    except GameSessionPlayer.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'GameSessionPlayer introuvable'}, status=404)

@csrf_exempt
@require_http_methods(["POST"])
@require_api_token
def api_create_player(request):
    import json
    data = json.loads(request.body.decode())
    name = data.get('name', '').strip()
    if not name:
        return JsonResponse({'success': False, 'error': 'Nom requis.'}, status=400)
    player, created = Player.objects.get_or_create(name=name)
    if not created:
        return JsonResponse({'success': False, 'error': 'Un joueur avec ce nom existe déjà.'}, status=409)
    return JsonResponse({'success': True, 'player_id': player.id})
