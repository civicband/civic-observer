"""Compare Notebooks counts against the migrated Research Projects."""

from django.core.management.base import BaseCommand

from notebooks.models import Notebook
from research.models import Coding, Memo, Project, ResultSet, SetItem


class Command(BaseCommand):
    help = "Verify notebook entry, note and tag counts against migrated projects."

    def add_arguments(self, parser):
        parser.add_argument("--notebook", dest="notebook", default=None)

    def handle(self, *args, **options):
        notebooks = Notebook.objects.all().order_by("name")
        if options["notebook"]:
            notebooks = notebooks.filter(id=options["notebook"])

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
            tag_count = sum(entry.tags.count() for entry in entries)
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

        if failures:
            self.stderr.write(f"{failures} notebook(s) failed verification")
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("All notebooks verified"))
