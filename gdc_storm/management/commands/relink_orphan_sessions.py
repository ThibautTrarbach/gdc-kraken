from django.core.management.base import BaseCommand

from gdc_storm.models import GameSession
from gdc_storm.utils import find_missions_for_session


class Command(BaseCommand):
    help = (
        "Rattache les GameSession orphelines (mission=NULL) aux Mission "
        "correspondantes (map/nom insensibles à la casse). Dry-run par défaut ; "
        "passer --apply pour écrire."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Applique les liaisons (sinon dry-run).',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        orphans = GameSession.objects.filter(mission__isnull=True).order_by('id')
        linked = 0
        skipped_none = 0
        skipped_ambiguous = 0

        mode = 'APPLY' if apply else 'DRY-RUN'
        self.stdout.write(f"[{mode}] {orphans.count()} session(s) orpheline(s).")

        for session in orphans.iterator():
            matches, _name, _version, map_normalized = find_missions_for_session(
                session.name, session.map
            )
            if len(matches) == 0:
                skipped_none += 1
                self.stdout.write(
                    f"  skip (aucune): id={session.id} name={session.name!r} "
                    f"map={session.map!r}"
                )
                continue
            if len(matches) > 1:
                skipped_ambiguous += 1
                ids = ', '.join(str(m.id) for m in matches)
                self.stdout.write(
                    f"  skip (ambiguës {len(matches)}): id={session.id} "
                    f"name={session.name!r} map={session.map!r} → missions [{ids}]"
                )
                continue

            mission = matches[0]
            self.stdout.write(
                f"  link: session id={session.id} → mission id={mission.id} "
                f"({mission.name!r} / {mission.map!r})"
                + ('' if apply else ' [dry-run]')
            )
            if apply:
                session.mission = mission
                if map_normalized:
                    session.map = map_normalized
                session.save(update_fields=['mission', 'map'])
            linked += 1

        self.stdout.write(self.style.SUCCESS(
            f"[{mode}] liées={linked} aucune={skipped_none} ambiguës={skipped_ambiguous}"
        ))
