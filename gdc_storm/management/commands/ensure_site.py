from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Assure que le Site Django (SITE_ID) a le bon domaine pour les callbacks OAuth."

    def handle(self, *args, **options):
        site_id = getattr(settings, "SITE_ID", 1)
        domain = getattr(settings, "SITE_DOMAIN", "localhost")
        name = getattr(settings, "SITE_NAME", "GDC Storm")

        site, created = Site.objects.update_or_create(
            id=site_id,
            defaults={"domain": domain, "name": name},
        )
        action = "créé" if created else "mis à jour"
        self.stdout.write(
            self.style.SUCCESS(
                f"Site {site_id} {action} : {site.domain} ({site.name})"
            )
        )
