# Generated manually for audit fixes (Player unique, Mission unique, indexes)

from django.conf import settings
from django.db import migrations, models


def dedupe_players(apps, schema_editor):
    Player = apps.get_model('gdc_storm', 'Player')
    GameSessionPlayer = apps.get_model('gdc_storm', 'GameSessionPlayer')
    Through = Player.users.through

    seen = {}
    for player in Player.objects.order_by('id'):
        name = player.name
        if name not in seen:
            seen[name] = player
            continue
        keep = seen[name]
        # Réaffecte les GameSessionPlayer vers le joueur conservé
        for gsp in GameSessionPlayer.objects.filter(player_id=player.id):
            conflict = GameSessionPlayer.objects.filter(
                session_id=gsp.session_id, player_id=keep.id
            ).exists()
            if conflict:
                gsp.delete()
            else:
                gsp.player_id = keep.id
                gsp.save(update_fields=['player_id'])
        # Réaffecte les liens User M2M
        for link in Through.objects.filter(player_id=player.id):
            Through.objects.get_or_create(player_id=keep.id, user_id=link.user_id)
            link.delete()
        player.delete()


def dedupe_missions(apps, schema_editor):
    Mission = apps.get_model('gdc_storm', 'Mission')
    GameSession = apps.get_model('gdc_storm', 'GameSession')

    seen = {}
    for mission in Mission.objects.order_by('id'):
        key = (mission.name, mission.map, mission.max_players)
        if key not in seen:
            seen[key] = mission
            continue
        keep = seen[key]
        GameSession.objects.filter(mission_id=mission.id).update(mission_id=keep.id)
        mission.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('gdc_storm', '0005_add_performance_indexes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(dedupe_players, migrations.RunPython.noop),
        migrations.RunPython(dedupe_missions, migrations.RunPython.noop),
        migrations.RemoveIndex(
            model_name='player',
            name='player_name_idx',
        ),
        migrations.AlterField(
            model_name='player',
            name='name',
            field=models.CharField(max_length=255, unique=True, verbose_name='Nom du joueur'),
        ),
        migrations.AddIndex(
            model_name='mission',
            index=models.Index(fields=['name'], name='mission_name_idx'),
        ),
        migrations.AddConstraint(
            model_name='mission',
            constraint=models.UniqueConstraint(
                fields=('name', 'map', 'max_players'),
                name='mission_name_map_max_players_uniq',
            ),
        ),
    ]
