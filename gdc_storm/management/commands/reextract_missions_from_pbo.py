from django.core.management.base import BaseCommand

from gdc_storm.admin_jobs import run_reextract_missions


class Command(BaseCommand):
    help = (
        "Régénère briefing, images de briefing, loadScreen et/ou marqueurs "
        "depuis les PBO déjà stockés (sans changer name/version/map). "
        "Par défaut : briefing + images + loadScreen + marqueurs. "
        "Passer --dry-run pour simuler."
    )

    def add_arguments(self, parser):
        parser.add_argument('--mission-id', type=int, help='Traiter une mission par ID.')
        parser.add_argument('--map', type=str, help='Filtrer par code carte (insensible à la casse).')
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simuler sans écrire sur disque ni en base.',
        )
        parser.add_argument(
            '--briefing-only',
            action='store_true',
            help='Ne régénérer que le briefing et ses images.',
        )
        parser.add_argument(
            '--markers-only',
            action='store_true',
            help='Ne régénérer que les marqueurs (JSON carte).',
        )
        parser.add_argument(
            '--loadscreen-only',
            action='store_true',
            help="Ne régénérer que l'image loadScreen.",
        )
        parser.add_argument(
            '--with-meta',
            action='store_true',
            help='Mettre aussi à jour authors / minPlayers / onLoadMission / overviewText.',
        )

    def handle(self, *args, **options):
        try:
            run_reextract_missions(
                dry_run=options['dry_run'],
                mission_id=options.get('mission_id'),
                map_code=options.get('map'),
                briefing_only=options['briefing_only'],
                markers_only=options['markers_only'],
                loadscreen_only=options['loadscreen_only'],
                with_meta=options['with_meta'],
                log=self.stdout.write,
            )
        except ValueError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
