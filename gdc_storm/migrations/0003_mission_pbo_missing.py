from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gdc_storm', '0002_alter_player_created_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='mission',
            name='pbo_missing',
            field=models.BooleanField(
                default=False,
                verbose_name='PBO manquant sur le serveur',
            ),
        ),
    ]
