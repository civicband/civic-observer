"""Ingest the f5bot feed or saved feed pages."""

import json
from contextlib import nullcontext
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from watch import ingest
from watch.models import IngestRun


class Command(BaseCommand):
    help = "Ingest the f5bot feed or saved feed pages."

    def add_arguments(self, parser):
        parser.add_argument("--file", action="append", default=[])
        parser.add_argument("--max-pages", type=int, default=None)
        parser.add_argument("--feed-url")
        parser.add_argument("--no-lock", action="store_true")

    def handle(self, *args, **options):
        if options["file"] and (
            options["feed_url"] or options["max_pages"] is not None
        ):
            raise CommandError(
                "--file cannot be combined with --feed-url or --max-pages"
            )
        lock = nullcontext(True) if options["no_lock"] else ingest.acquire_ingest_lock()
        # A missing lock context also denotes an unavailable lock.
        with lock if lock is not None else nullcontext(False) as acquired:
            if not acquired:
                self.stderr.write(self.style.WARNING("Ingest lock is held; skipping."))
                return
            if options["file"]:
                failed = False
                for path in options["file"]:
                    if self.ingest_file(Path(path)).error:
                        failed = True
                if failed:
                    raise CommandError("File ingest failed; see IngestRun for details")
            else:
                url = options["feed_url"] or settings.F5BOT_FEED_URL
                if not url:
                    raise CommandError("F5BOT_FEED_URL is not set")
                run = ingest.run_ingest(
                    url,
                    max_pages=20
                    if options["max_pages"] is None
                    else options["max_pages"],
                )
                if run.error:
                    raise CommandError("Feed ingest failed; see IngestRun for details")

    def ingest_file(self, path):
        run = IngestRun.objects.create(source=f"file:{path.name}")
        try:
            page = json.loads(path.read_text())
            run.seen, run.new, run.skipped = ingest.ingest_page(page)
            run.pages = 1
        except Exception as exc:
            # Do not put file contents, paths, or potential secrets in errors.
            run.error = type(exc).__name__
        finally:
            run.finished_at = timezone.now()
            run.save()
        return run
