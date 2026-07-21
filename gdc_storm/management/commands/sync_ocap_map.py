from django.core.management.base import BaseCommand

from gdc_storm.models import Mission
from gdc_storm.ocap_maps import (
    fetch_ocap_archives,
    is_local_map_ready,
    resolve_ocap_world_meta,
    sync_ocap_world,
)


class Command(BaseCommand):
    help = (
        "Télécharge et extrait les packs de tuiles OCAP2 (.7z) pour un world, "
        "une mission, ou tous les worldName utilisés en base. "
        "Passer --dry-run pour simuler."
    )

    def add_arguments(self, parser):
        parser.add_argument('--world', type=str, help='worldName OCAP (ex. altis).')
        parser.add_argument('--mission-id', type=int, help='Résoudre le world depuis une mission.')
        parser.add_argument(
            '--all-used',
            action='store_true',
            help='Sync tous les worldName déductibles des Mission.map en base.',
        )
        parser.add_argument(
            '--report',
            action='store_true',
            help='Afficher la couverture OCAP / Arma3Map / aucun pour les Mission.map distincts.',
        )
        parser.add_argument('--force', action='store_true', help='Re-télécharger même si présent.')
        parser.add_argument('--dry-run', action='store_true', help='Simuler sans écrire.')

    def handle(self, *args, **options):
        if options['report']:
            self._print_coverage_report()
            if not (options.get('world') or options.get('mission_id') is not None or options['all_used']):
                return

        dry_run = options['dry_run']
        force = options['force']
        worlds: list[str] = []

        if options.get('world'):
            worlds.append(options['world'].strip())
        if options.get('mission_id') is not None:
            mission = Mission.objects.filter(id=options['mission_id']).first()
            if not mission:
                self.stderr.write(self.style.ERROR('Mission introuvable.'))
                return
            resolved = resolve_ocap_world_meta(mission.map)
            if not resolved:
                self.stderr.write(self.style.ERROR(
                    f'Aucun world OCAP pour mission.map={mission.map!r}'
                ))
                return
            worlds.append(resolved[0])
        if options['all_used']:
            map_codes = (
                Mission.objects.exclude(map='')
                .values_list('map', flat=True)
                .distinct()
            )
            seen = set()
            for code in map_codes:
                resolved = resolve_ocap_world_meta(code)
                if not resolved:
                    continue
                name = resolved[0]
                if name.lower() in seen:
                    continue
                seen.add(name.lower())
                worlds.append(name)

        worlds = list(dict.fromkeys(w for w in worlds if w))
        if not worlds:
            if options['report']:
                return
            self.stderr.write(self.style.ERROR(
                'Préciser --world, --mission-id, --all-used ou --report.'
            ))
            return

        mode = 'DRY-RUN' if dry_run else 'APPLY'
        self.stdout.write(f'[{mode}] {len(worlds)} world(s) — catalogue archives…')
        archives = fetch_ocap_archives()
        self.stdout.write(f'  archives connues: {len(archives)}')

        ok = err = skip = 0
        for world in worlds:
            label = world
            if is_local_map_ready(world) and not force and not dry_run:
                skip += 1
                self.stdout.write(f'  skip (local OK): {label}')
                continue
            result = sync_ocap_world(world, force=force, dry_run=dry_run)
            status = result.get('status')
            if status in ('ready', 'would-sync'):
                ok += 1
                style = self.style.WARNING if dry_run else self.style.SUCCESS
                detail = result.get('reason') or result.get('path') or result.get('url') or ''
                self.stdout.write(style(f'  {status}: {label} {detail}'))
            elif status == 'queued':
                skip += 1
                self.stdout.write(f'  queued: {label}')
            else:
                err += 1
                self.stdout.write(self.style.ERROR(
                    f'  error: {label} — {result.get("error")}'
                ))

        self.stdout.write(self.style.SUCCESS(
            f'[{mode}] ok={ok} skip={skip} err={err}'
        ))

    def _print_coverage_report(self):
        from gdc_storm.arma3map import get_arma3map_config
        from gdc_storm.ocap_maps import resolve_ocap_world_meta

        codes = sorted({
            (c or '').strip().lower()
            for c in Mission.objects.exclude(map='').values_list('map', flat=True).distinct()
            if (c or '').strip()
        })
        ocap_n = arma_n = none_n = 0
        self.stdout.write(f'[REPORT] {len(codes)} code(s) Mission.map distincts')
        for code in codes:
            if resolve_ocap_world_meta(code):
                ocap_n += 1
                tag = 'ocap'
            elif get_arma3map_config(code):
                arma_n += 1
                tag = 'arma3map'
            else:
                none_n += 1
                tag = 'aucun'
                self.stdout.write(f'  missing: {code}')
            if tag != 'aucun':
                self.stdout.write(f'  {tag}: {code}')
        self.stdout.write(self.style.SUCCESS(
            f'[REPORT] ocap={ocap_n} arma3map={arma_n} aucun={none_n}'
        ))
