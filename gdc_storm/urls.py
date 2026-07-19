
from django.urls import path
from . import views
from . import legacy_views
from django.contrib.auth import views as auth_views
from django.http import HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from gdc_storm import apis

# Redirection GET /logout vers l'accueil
logout_redirect = lambda request: HttpResponseRedirect('/')

urlpatterns = [
    path('changer-mot-de-passe/', views.change_password, name='change_password'),
    path('compte/connexions/', views.account_connections, name='account_connections'),
    path('compte/connexions/<str:provider>/delier/', views.disconnect_social_account, name='disconnect_social_account'),
    path('compte/en-attente/', views.pending_approval, name='pending_approval'),
    path('', views.home, name='home'),
    path('upload/', views.upload_mission, name='upload_mission'),
    path('upload/analyze/', views.upload_analyze, name='upload_analyze'),
    path('upload/commit/', views.upload_commit, name='upload_commit'),
    path('recup/', views.recup_missions, name='recup_missions'),
    path('recup/prefetch/', views.recup_prefetch, name='recup_prefetch'),
    path('recup/analyze/', views.recup_analyze, name='recup_analyze'),
    path('recup/commit/', views.recup_commit, name='recup_commit'),
    path('recup/scan-pbo/', views.scan_pbo_missing, name='scan_pbo_missing'),
    path('recup/scan-pbo/run/', views.scan_pbo_missing_run, name='scan_pbo_missing_run'),
    path('recup/scan-pbo/apply/', views.scan_pbo_missing_apply, name='scan_pbo_missing_apply'),
    path('missions/', views.mission_list, name='mission_list'),
    path('missions/<int:mission_id>/', views.mission_detail, name='mission_detail'),
    path('missions/<int:mission_id>/delete/', views.delete_mission, name='delete_mission'),
    path('login/', auth_views.LoginView.as_view(template_name='gdc_storm/login.html'), name='login'),
    path('logout/', logout_redirect, name='logout'),  # GET redirige vers home
    path('logout-post/', auth_views.LogoutView.as_view(), name='logout_post'),  # POST seulement
    path('users/<int:user_id>/', views.user_profile, name='user_profile'),
    path('players/', views.player_list, name='player_list'),
    path('players/admin/', views.player_admin, name='player_admin'),
    path('players/<int:player_id>/', views.player_detail, name='player_detail'),
    path('stats/', views.stats, name='stats'),
    path('roles/', views.role_categories, name='role_categories'),
    path('player-mapping/', views.player_mapping, name='player_mapping'),
    path('api/gamesessions/', apis.api_create_gamesession, name='api_create_gamesession'),
    path('api/gamesessions/<int:session_id>/end/', apis.api_update_gamesession_end, name='api_update_gamesession_end'),
    path('api/gamesessions/<int:session_id>/add_player/', apis.api_add_gamesession_player, name='api_add_gamesession_player'),
    path('api/gamesessions/<int:session_id>/update_player_status/', apis.api_update_gamesession_player_status, name='api_update_gamesession_player_status'),
    path('api/players/', apis.api_create_player, name='api_create_player'),
    path('sessions/', views.session_list, name='session_list'),
    path('sessions/<int:session_id>/', views.session_detail, name='session_detail'),
    path('sessions/orphelines/', views.orphan_sessions, name='orphan_sessions'),
    path('maps/', views.map_list, name='map_list'),
    path('maps/<int:map_id>/', views.map_detail, name='map_detail'),
    path('legacy/', legacy_views.legacy_export, name='legacy_export'),
    path('legacy/bulk_missions/', legacy_views.bulk_missions, name='bulk_missions'),
    path('legacy/bulk_upload/', legacy_views.bulk_upload_mission, name='bulk_upload_mission'),
    path('legacy/update_linked_user/', legacy_views.update_linked_user, name='update_linked_user'),
    path('legacy/create_user_from_linkeduser/', legacy_views.create_user_from_linkeduser, name='create_user_from_linkeduser'),
    path('legacy/export_legacy_missions_to_main/', legacy_views.export_legacy_missions_to_main, name='export_legacy_missions_to_main'),
    path('legacy/get_legacy_import_errors/', legacy_views.get_legacy_import_errors, name='get_legacy_import_errors'),
    path('legacy/delete_legacy_import_error/', legacy_views.delete_legacy_import_error, name='delete_legacy_import_error'),
    path('legacy/import_players_csv/', legacy_views.import_players_csv, name='import_players_csv'),
    path('legacy/import_roles_csv/', legacy_views.import_roles_csv, name='import_roles_csv'),
    path('legacy/import_gamesessions_csv/', legacy_views.import_gamesessions_csv, name='import_gamesessions_csv'),
    path('legacy/import_mapnames_csv/', legacy_views.import_mapnames_csv, name='import_mapnames_csv'),
    path('legacy/clear_legacy_missions/', legacy_views.clear_legacy_missions, name='clear_legacy_missions'),
    path('legacy/clear_legacy_dbs/', legacy_views.clear_legacy_dbs, name='clear_legacy_dbs'),
    path('legacy/import_gamesession_player_role_csv/', legacy_views.import_gamesession_player_role_csv, name='import_gamesession_player_role_csv'),
    path('legacy/import_legacy_gamesessions/', legacy_views.import_legacy_gamesessions, name='import_legacy_gamesessions'),
]
