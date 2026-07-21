import os

from django.core.management.base import BaseCommand

from gdc_storm.models import Mission
from gdc_storm.pbo_extract import (
    delete_markers_file,
    extract_markers_from_pbo,
    markers_abs_path,
    save_markers_to_storage,
)
from gdc_storm.views import mission_pbo_candidate_names, resolve_pbo_on_disk


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
        from django.conf import settings
        from yapbol import PBOFile

        dry_run = options['dry_run']
        force = options['force'] or options['repair_missing']
        repair_missing = options['repair_missing']
        mission_id = options.get('mission_id')
        map_code = options.get('map')

        qs = Mission.objects.all().order_by('id')
        if mission_id is not None:
            qs = qs.filter(id=mission_id)
        if map_code:
            qs = qs.filter(map__iexact=map_code.strip())
        if repair_missing:
            qs = qs.exclude(markers_file='')

        processed = updated = skipped = errors = 0
        mode = 'DRY-RUN' if dry_run else 'APPLY'
        self.stdout.write(f'[{mode}] {qs.count()} mission(s) a traiter.')

        storage_root = settings.MISSIONS_PBO_STORAGE_PATH
        for mission in qs.iterator():
            processed += 1
            label = f'id={mission.id} {mission.name!r} map={mission.map!r}'

            if repair_missing and mission.markers_file:
                if markers_abs_path(mission.markers_file).is_file():
                    skipped += 1
                    self.stdout.write(f'  skip (fichier OK): {label}')
                    continue
                self.stdout.write(f'  repair (JSON absent): {label} ({mission.markers_file})')

            if mission.markers_file and not force:
                skipped += 1
                self.stdout.write(f'  skip (deja extrait): {label}')
                continue

            pbo_name = resolve_pbo_on_disk(mission_pbo_candidate_names(mission))
            if not pbo_name:
                skipped += 1
                self.stdout.write(f'  skip (PBO absent): {label}')
                continue

            pbo_path = os.path.join(storage_root, pbo_name)
            try:
                pbo = PBOFile.read_file(pbo_path)
            except Exception as exc:
                errors += 1
                self.stdout.write(self.style.ERROR(f'  erreur lecture PBO: {label} — {exc}'))
                continue

            markers, problems = extract_markers_from_pbo(pbo)
            if problems and not markers:
                skipped += 1
                reason = problems[0]
                self.stdout.write(f'  skip ({reason}): {label}')
                if len(problems) > 1:
                    for extra in problems[1:3]:
                        self.stdout.write(f'         {extra}')
                if force and not dry_run:
                    delete_markers_file(mission.markers_file)
                    mission.markers_file = ''
                    mission.save(update_fields=['markers_file'])
                    self.stdout.write(f'  force: markers_file vide pour {label}')
                continue

            old_path = mission.markers_file or ''
            path = save_markers_to_storage(markers, mission_id=mission.id) if markers else None
            rel_path = path or ''

            if dry_run:
                self.stdout.write(
                    f'  would write {len(markers)} marqueur(s) -> {rel_path or "(vide)"}: {label}'
                )
                if problems:
                    for note in problems[:3]:
                        self.stdout.write(f'         note: {note}')
                updated += 1
                continue

            # Ne pas supprimer le fichier qu'on vient d'écrire (même chemin missions/markers/{id}.json).
            if old_path and old_path != rel_path:
                delete_markers_file(old_path)
            elif not rel_path and old_path:
                delete_markers_file(old_path)

            mission.markers_file = rel_path
            mission.save(update_fields=['markers_file'])
            updated += 1
            if markers:
                abs_written = markers_abs_path(rel_path)
                if not abs_written.is_file():
                    errors += 1
                    self.stdout.write(self.style.ERROR(
                        f'  erreur ecriture disque: {label} ({abs_written})'
                    ))
                    continue
                self.stdout.write(
                    self.style.SUCCESS(
                        f'  ok: {len(markers)} marqueur(s) -> {rel_path}: {label}'
                    )
                )
                self.stdout.write(f'         disk: {abs_written}')
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f'  ok: 0 marqueur(s) -> (vide): {label}'
                    )
                )
            if problems:
                for note in problems[:3]:
                    self.stdout.write(f'         note: {note}')

        self.stdout.write(self.style.SUCCESS(
            f'[{mode}] traitees={processed} mises_a_jour={updated} '
            f'ignorees={skipped} erreurs={errors}'
        ))
