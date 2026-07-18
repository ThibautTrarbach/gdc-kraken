from django.core.management.base import BaseCommand

from gdc_storm.views import RECUP_USERNAME, get_recup_user


class Command(BaseCommand):
    help = f"Crée le compte dédié {RECUP_USERNAME} s'il n'existe pas."

    def handle(self, *args, **options):
        user = get_recup_user()
        self.stdout.write(self.style.SUCCESS(
            f"Compte {user.username} prêt (id={user.id}, is_active={user.is_active})."
        ))
