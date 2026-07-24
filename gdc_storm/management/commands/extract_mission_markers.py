from django.core.management.base import BaseCommand

from gdc_storm.admin_jobs import run_extract_mission_markers


class Command(BaseCommand):
    help = (
        "Extrait les marqueurs éditeur depuis les PBO stockés et écrit un JSON "
        "par mission (markers_file). Passer --dry-run pour simuler sans écrire. "
        "Les missions binarisées sont ignorées (--force peut vider markers_file)."
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
            '--force',
            action='store_true',
            help='Réécrire même si markers_file est déjà renseigné.',
        )
        parser.add_argument(
            '--repair-missing',
            action='store_true',
            help='Ré-extraire les missions dont le JSON marqueurs est absent du disque.',
        )

    def handle(self, *args, **options):
        run_extract_mission_markers(
            dry_run=options['dry_run'],
            mission_id=options.get('mission_id'),
            map_code=options.get('map'),
            force=options['force'],
            repair_missing=options['repair_missing'],
            log=self.stdout.write,
        )