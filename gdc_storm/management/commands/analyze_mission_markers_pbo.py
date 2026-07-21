import json
import os

from django.core.management.base import BaseCommand

from gdc_storm.models import Mission
from gdc_storm.pbo_extract import diagnose_pbo_markers
from gdc_storm.views import mission_pbo_candidate_names, resolve_pbo_on_disk


class Command(BaseCommand):
    help = (
        'Analyse un PBO mission : marqueurs SQM, masquages SQF, configs forme/taille, '
        'résultat final. Utile pour debugger des marqueurs MM encore visibles sur la carte.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--pbo-path', type=str, help='Chemin absolu vers un fichier .pbo')
        parser.add_argument('--mission-id', type=int, help='ID mission Storm (PBO sur le volume)')
        parser.add_argument('--json', action='store_true', help='Sortie JSON brute')

    def handle(self, *args, **options):
        from django.conf import settings
        from yapbol import PBOFile

        pbo_path = options.get('pbo_path')
        mission_id = options.get('mission_id')

        if not pbo_path and mission_id is None:
            self.stderr.write(self.style.ERROR('Préciser --pbo-path ou --mission-id'))
            return

        if not pbo_path:
            mission = Mission.objects.filter(id=mission_id).first()
            if not mission:
                self.stderr.write(self.style.ERROR(f'Mission id={mission_id} introuvable'))
                return
            pbo_name = resolve_pbo_on_disk(mission_pbo_candidate_names(mission))
            if not pbo_name:
                self.stderr.write(self.style.ERROR(f'PBO absent pour mission id={mission_id}'))
                return
            pbo_path = os.path.join(settings.MISSIONS_PBO_STORAGE_PATH, pbo_name)
            label = f'id={mission.id} {mission.name!r}'
        else:
            label = pbo_path

        if not os.path.isfile(pbo_path):
            self.stderr.write(self.style.ERROR(f'Fichier introuvable: {pbo_path}'))
            return

        pbo = PBOFile.read_file(pbo_path)
        report = diagnose_pbo_markers(pbo)

        if options['json']:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
            return

        self.stdout.write(self.style.SUCCESS(f'Analyse PBO — {label}'))
        self.stdout.write(f"Scripts démarrage ({len(report['startup_scripts'])}):")
        for path in report['startup_scripts']:
            self.stdout.write(f'  - {path}')

        self.stdout.write(f"\nMarqueurs SQM visibles: {report['sqm_visible_count']}")
        for note in report['sqm_problems']:
            self.stdout.write(f'  note SQM: {note}')

        if report['script_hidden']:
            self.stdout.write(f"\nMasqués par SQF ({len(report['script_hidden'])}):")
            for name in report['script_hidden']:
                self.stdout.write(f'  - {name}')
        else:
            self.stdout.write('\nMasqués par SQF: (aucun nom littéral détecté)')

        if report['script_configs']:
            self.stdout.write(f"\nConfigs SQF ({len(report['script_configs'])}):")
            for name, cfg in sorted(report['script_configs'].items()):
                self.stdout.write(f'  - {name}: {cfg}')

        leaked = []
        for marker in report['sqm_markers']:
            name = (marker.get('name') or '').strip()
            text = (marker.get('text') or '').strip()
            if name in report['script_hidden']:
                continue
            if text and any(
                kw in text.lower()
                for kw in ('hélico', 'helico', 'mm_', 'patrol', 'editor', 'debug')
            ):
                leaked.append(marker)

        if leaked:
            self.stdout.write('\nMarqueurs suspects encore affichés (non détectés comme cachés):')
            for marker in leaked:
                self.stdout.write(
                    f"  - name={marker.get('name')!r} text={marker.get('text')!r} "
                    f"type={marker.get('type')!r}"
                )

        self.stdout.write(f"\nRésultat final carte: {report['final_count']} marqueur(s)")
        for note in report['final_problems']:
            self.stdout.write(f'  note: {note}')
