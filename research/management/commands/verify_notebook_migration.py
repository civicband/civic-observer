"""Compare Notebooks counts against the migrated Research Projects."""

import uuid

from django.core.management.base import BaseCommand, CommandError

from notebooks.models import Notebook, Tag
from research.models import Code, Coding, Memo, Project, ResultSet, SetItem


class Command(BaseCommand):
    help = "Verify notebook entry, note and tag counts against migrated projects."

    def add_arguments(self, parser):
        parser.add_argument("--notebook", dest="notebook", default=None)

    def handle(self, *args, **options):
        notebooks = (
            Notebook.objects.all().order_by("name").prefetch_related("entries__tags")
        )
        if options["notebook"]:
            try:
                notebook_id = uuid.UUID(options["notebook"])
            except ValueError as exc:
                raise CommandError("Invalid notebook id") from exc
            notebooks = notebooks.filter(id=notebook_id)

        failures = 0
        for notebook in notebooks:
            project = Project.objects.filter(id=notebook.id).first()
            if project is None:
                failures += 1
                self.stdout.write(
                    self.style.ERROR(f"MISSING {notebook.name} ({notebook.id})")
                )
                continue

            result_set = ResultSet.objects.filter(
                project=project, origin=ResultSet.Origin.IMPORT
            ).first()
            entries = list(notebook.entries.all())
            entry_count = len(entries)
            note_count = sum(1 for entry in entries if (entry.note or "").strip())
            tag_count = sum(len(entry.tags.all()) for entry in entries)
            setitem_count = (
                SetItem.objects.filter(result_set=result_set).count()
                if result_set
                else 0
            )
            memo_count = Memo.objects.filter(project=project).count()
            coding_count = Coding.objects.filter(project=project).count()

            mismatches = []
            if setitem_count != entry_count:
                mismatches.append(f"entries {setitem_count}/{entry_count}")
            if memo_count != note_count:
                mismatches.append(f"notes {memo_count}/{note_count}")
            if coding_count != tag_count:
                mismatches.append(f"tags {coding_count}/{tag_count}")
            if mismatches:
                failures += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"MISMATCH {notebook.name} ({notebook.id}): "
                        + ", ".join(mismatches)
                    )
                )
            else:
                self.stdout.write(
                    f"OK {notebook.name} ({notebook.id}): "
                    f"{entry_count} entries, {note_count} notes, {tag_count} tags"
                )

        failures += self._verify_orphan_tags()

        if failures:
            self.stderr.write(f"{failures} notebook(s) failed verification")
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("All notebooks verified"))

    def _verify_orphan_tags(self) -> int:
        """Verify tags used nowhere were copied to a project-owned code."""
        orphan_tags = Tag.objects.filter(entries__isnull=True)
        failures = 0
        user_ids = list(
            orphan_tags.order_by().values_list("user_id", flat=True).distinct()
        )
        for user_id in user_ids:
            names = set(
                orphan_tags.filter(user_id=user_id).values_list("name", flat=True)
            )
            if user_id is None:
                failures += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"UNMIGRATED {len(names)} tag(s) have no owning user"
                    )
                )
                continue
            migrated = set(
                Code.objects.filter(
                    project__owner_id=user_id, name__in=names
                ).values_list("name", flat=True)
            )
            missing = names - migrated
            if missing:
                failures += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"MISMATCH orphan tags for user {user_id}: "
                        f"{len(missing)} of {len(names)} missing"
                    )
                )
            else:
                self.stdout.write(
                    f"OK orphan tags for user {user_id}: {len(names)} code(s)"
                )
        return failures
