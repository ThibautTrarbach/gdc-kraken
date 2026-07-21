import os

from django.core.management.base import BaseCommand

from gdc_storm.models import Mission
from gdc_storm.views import (
    mission_pbo_candidate_names,
    reextract_mission_content_from_pbo,
    resolve_pbo_on_disk,
)


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
        from django.conf import settings
        from yapbol import PBOFile

        dry_run = options['dry_run']
        mission_id = options.get('mission_id')
        map_code = options.get('map')
        with_meta = options['with_meta']

        only_flags = [
            options['briefing_only'],
            options['markers_only'],
            options['loadscreen_only'],
        ]
        if sum(1 for f in only_flags if f) > 1:
            self.stderr.write(self.style.ERROR(
                'Utiliser au plus un de --briefing-only / --markers-only / --loadscreen-only.'
            ))
            return

        if options['briefing_only']:
            do_briefing, do_markers, do_loadscreen = True, False, False
        elif options['markers_only']:
            do_briefing, do_markers, do_loadscreen = False, True, False
        elif options['loadscreen_only']:
            do_briefing, do_markers, do_loadscreen = False, False, True
        else:
            do_briefing, do_markers, do_loadscreen = True, True, True

        qs = Mission.objects.all().order_by('id')
        if mission_id is not None:
            qs = qs.filter(id=mission_id)
        if map_code:
            qs = qs.filter(map__iexact=map_code.strip())

        processed = updated = skipped = errors = 0
        mode = 'DRY-RUN' if dry_run else 'APPLY'
        parts = []
        if do_briefing:
            parts.append('briefing')
        if do_loadscreen:
            parts.append('loadScreen')
        if do_markers:
            parts.append('marqueurs')
        if with_meta:
            parts.append('meta')
        self.stdout.write(
            f'[{mode}] {qs.count()} mission(s) — {", ".join(parts)}'
        )

        storage_root = settings.MISSIONS_PBO_STORAGE_PATH
        for mission in qs.iterator():
            processed += 1
            label = f'id={mission.id} {mission.name!r} map={mission.map!r}'

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

            try:
                summary, notes = reextract_mission_content_from_pbo(
                    mission,
                    pbo,
                    briefing=do_briefing,
                    markers=do_markers,
                    loadscreen=do_loadscreen,
                    meta=with_meta,
                    dry_run=dry_run,
                )
            except Exception as exc:
                errors += 1
                self.stdout.write(self.style.ERROR(f'  erreur reextract: {label} — {exc}'))
                continue

            updated += 1
            detail_bits = []
            if summary.get('briefing_items') is not None:
                detail_bits.append(f"briefing={summary['briefing_items']}")
            if summary.get('briefing_images') is not None:
                detail_bits.append(f"imgs={summary['briefing_images']}")
            if summary.get('loadscreen') is not None:
                ls = summary['loadscreen']
                detail_bits.append(f"loadScreen={'oui' if ls and ls != 'none' else 'non'}")
            if summary.get('markers') is not None:
                detail_bits.append(f"marqueurs={summary['markers']}")
            if summary.get('meta'):
                detail_bits.append('meta')
            detail = ', '.join(detail_bits) or 'ok'
            verb = 'would' if dry_run else 'ok'
            style = self.style.WARNING if dry_run else self.style.SUCCESS
            self.stdout.write(style(f'  {verb}: {detail}: {label} (pbo={pbo_name})'))
            for note in notes[:5]:
                self.stdout.write(f'         note: {note}')

        self.stdout.write(self.style.SUCCESS(
            f'[{mode}] traitees={processed} mises_a_jour={updated} '
            f'ignorees={skipped} erreurs={errors}'
        ))
