"""
Management command for resilient backfill of meeting data.

Usage:
    python manage.py resilient_backfill --subdomain=oakland.ca
    python manage.py resilient_backfill --subdomain=all --document-type=both
    python manage.py resilient_backfill --subdomain=all --resume
"""

from django.core.management.base import BaseCommand, CommandError

from meetings.models import BackfillJob
from meetings.resilient_backfill import ResilientBackfillService
from municipalities.models import Muni


class Command(BaseCommand):
    help = "Backfill meeting data with checkpoint/resume capability"

    def add_arguments(self, parser):
        parser.add_argument(
            "--subdomain",
            type=str,
            required=True,
            help='Municipality subdomain to backfill (or "all" for all municipalities)',
        )
        parser.add_argument(
            "--document-type",
            type=str,
            choices=["agenda", "minutes", "both"],
            default="both",
            help="Document type to backfill",
        )
        parser.add_argument(
            "--resume",
            action="store_true",
            help="Resume failed/paused jobs instead of creating new ones",
        )
        parser.add_argument(
            "--verify-only",
            action="store_true",
            help="Only verify existing data without fetching",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Number of records to fetch per API call",
        )

    def handle(self, *args, **options):
        subdomain = options["subdomain"]
        doc_type = options["document_type"]
        resume = options["resume"]
        verify_only = options["verify_only"]
        batch_size = options["batch_size"]

        # Get municipalities to process
        if subdomain == "all":
            munis = Muni.objects.all()
        else:
            munis = Muni.objects.filter(subdomain=subdomain)

        if not munis.exists():
            raise CommandError(f"No municipalities found for subdomain: {subdomain}")

        # Process each municipality
        for muni in munis:
            self.stdout.write(f"\nProcessing {muni.subdomain}...")

            # Determine which document types to process
            doc_types = ["agenda", "minutes"] if doc_type == "both" else [doc_type]

            for dt in doc_types:
                if resume:
                    job = self._resume_job(muni, dt)
                else:
                    job = self._create_job(muni, dt)

                if verify_only:
                    self._verify_job(job)
                else:
                    self._run_job(job, batch_size)

    def _create_job(self, muni: Muni, doc_type: str) -> BackfillJob:
        """Create a new backfill job.

        Checks for concurrent backfills before creating.
        """
        from datetime import timedelta

        from django.utils import timezone

        from meetings.models import BackfillProgress

        # Check for active BackfillJob
        active_job = BackfillJob.objects.filter(
            municipality=muni,
            document_type=doc_type,
            status__in=["pending", "running"],
        ).first()

        if active_job:
            self.stdout.write(
                self.style.WARNING(
                    f"  ⚠️  Backfill already in progress for {muni.subdomain} {doc_type}\n"
                    f"     Active BackfillJob: {active_job.id} (status: {active_job.status})\n"
                    f"     Started: {timezone.now() - active_job.created} ago\n"
                    f"     Check /admin/meetings/backfilljob/ for details"
                )
            )
            raise CommandError(
                f"Backfill already running for {muni.subdomain} {doc_type}. "
                "Mark it as failed in admin if it's stuck."
            )

        # Check for active BackfillProgress (webhook-triggered)
        progress = BackfillProgress.objects.filter(
            municipality=muni,
            document_type=doc_type,
            status="in_progress",
        ).first()

        if progress:
            # Check if stale
            stale_threshold = timezone.now() - timedelta(hours=1)
            if progress.updated_at < stale_threshold:
                self.stdout.write(
                    self.style.WARNING(
                        f"  ⚠️  Detected stale BackfillProgress (last update: {progress.updated_at})\n"
                        f"     Marking as failed and proceeding with new backfill"
                    )
                )
                progress.status = "failed"
                progress.error_message = "Job timed out (no update in 1+ hour)"
                progress.save()
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"  ⚠️  Backfill already in progress for {muni.subdomain} {doc_type}\n"
                        f"     Active BackfillProgress (webhook-triggered)\n"
                        f"     Mode: {progress.mode}, Started: {timezone.now() - progress.updated_at} ago\n"
                        f"     Check /admin/meetings/backfillprogress/ for details"
                    )
                )
                raise CommandError(
                    f"Webhook-triggered backfill already running for {muni.subdomain} {doc_type}. "
                    "Wait for it to complete or mark as failed in admin."
                )

        job = BackfillJob.objects.create(
            municipality=muni,
            document_type=doc_type,
            status="pending",
        )
        self.stdout.write(f"  Created job {job.id} for {doc_type}")
        return job

    def _resume_job(self, muni: Muni, doc_type: str) -> BackfillJob:
        """Resume an existing failed/paused job or create new one."""
        job = (
            BackfillJob.objects.filter(
                municipality=muni,
                document_type=doc_type,
                status__in=["failed", "paused"],
            )
            .order_by("-created")
            .first()
        )

        if job:
            self.stdout.write(
                self.style.WARNING(f"  Resuming job {job.id} from cursor position")
            )
        else:
            job = self._create_job(muni, doc_type)

        return job

    def _run_job(self, job: BackfillJob, batch_size: int) -> None:
        """Run a backfill job."""
        try:
            with ResilientBackfillService(job, batch_size=batch_size) as service:
                service.run()

            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✓ {job.document_type}: "
                    f"{job.pages_created} created, {job.pages_updated} updated, "
                    f"{job.errors_encountered} errors"
                )
            )

            # Show verification results
            if job.expected_count and job.actual_count:
                if job.actual_count == job.expected_count:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  ✓ Verified: {job.actual_count}/{job.expected_count} pages"
                        )
                    )
                else:
                    missing = job.expected_count - job.actual_count
                    self.stdout.write(
                        self.style.ERROR(
                            f"  ✗ Missing {missing} pages! "
                            f"({job.actual_count}/{job.expected_count})"
                        )
                    )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  ✗ Failed: {e}"))

    def _verify_job(self, job: BackfillJob) -> None:
        """Verify an existing job without fetching."""
        with ResilientBackfillService(job) as service:
            service._verify_completeness()

        if job.actual_count is not None and job.expected_count is not None:
            if job.actual_count == job.expected_count:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ {job.document_type}: "
                        f"{job.actual_count}/{job.expected_count} pages"
                    )
                )
            else:
                missing = job.expected_count - job.actual_count
                self.stdout.write(
                    self.style.ERROR(
                        f"  ✗ {job.document_type}: Missing {missing} pages! "
                        f"({job.actual_count}/{job.expected_count})"
                    )
                )
