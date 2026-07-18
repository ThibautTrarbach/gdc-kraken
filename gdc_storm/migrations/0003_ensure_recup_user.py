from django.contrib.auth.hashers import make_password
from django.db import migrations


RECUP_USERNAME = 'GDC-RECUP'


def create_recup_user(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    if User.objects.filter(username=RECUP_USERNAME).exists():
        return
    User.objects.create(
        username=RECUP_USERNAME,
        email='',
        password=make_password(None),
        is_active=False,
        is_staff=False,
        is_superuser=False,
    )


def remove_recup_user(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    User.objects.filter(username=RECUP_USERNAME, is_active=False).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('gdc_storm', '0002_alter_player_created_at'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(create_recup_user, remove_recup_user),
    ]
