from django.db import migrations


def backfill_role_categories(apps, schema_editor):
    GameSessionPlayer = apps.get_model('gdc_storm', 'GameSessionPlayer')
    RoleCategory = apps.get_model('gdc_storm', 'RoleCategory')
    roles = (
        GameSessionPlayer.objects.exclude(role='')
        .exclude(role__isnull=True)
        .values_list('role', flat=True)
        .distinct()
    )
    RoleCategory.objects.bulk_create(
        [RoleCategory(role_name=role, category=role) for role in roles],
        ignore_conflicts=True,
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('gdc_storm', '0007_rolecategory'),
    ]

    operations = [
        migrations.RunPython(backfill_role_categories, noop_reverse),
    ]
