import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from model_utils.models import TimeStampedModel


class Project(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="research_projects",
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["-modified"]

    def __str__(self) -> str:
        return self.name


class ResultSet(TimeStampedModel):
    class Origin(models.TextChoices):
        QUERY = "query", "Query"
        LENS = "lens", "Lens"
        MANUAL = "manual", "Manual"
        IMPORT = "import", "Import"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        MATERIALIZING = "materializing", "Materializing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        TOO_LARGE = "too_large", "Too large"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="sets")
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, related_name="children"
    )
    name = models.CharField(max_length=200)
    origin = models.CharField(max_length=16, choices=Origin, default=Origin.QUERY)
    query = models.JSONField(default=dict)
    query_hash = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status, default=Status.PENDING)
    item_count = models.IntegerField(default=0)
    materialized_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self) -> str:
        return self.name


class Code(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="codes")
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="children"
    )
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, blank=True)
    definition = models.TextField(blank=True)
    inclusion = models.TextField(blank=True)
    exclusion = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "parent", "name"],
                nulls_distinct=False,
                name="research_code_uniq_name",
            )
        ]

    def __str__(self) -> str:
        return self.name


class IgnoreRule(TimeStampedModel):
    class JobAction(models.TextChoices):
        APPLY = "apply", "Apply"
        REVERT = "revert", "Revert"

    class JobStatus(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="ignore_rules"
    )
    name = models.CharField(max_length=200)
    predicate = models.JSONField(default=dict)
    reason = models.CharField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    last_job_id = models.CharField(max_length=64, blank=True, default="")
    last_job_action = models.CharField(
        max_length=10, choices=JobAction, blank=True, default=""
    )
    last_job_status = models.CharField(
        max_length=10, choices=JobStatus, blank=True, default=""
    )
    last_job_error = models.TextField(blank=True, default="")
    last_job_count = models.IntegerField(null=True, blank=True)
    last_job_finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self) -> str:
        return self.name


class AuditEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    verb = models.CharField(max_length=50)
    result_set = models.ForeignKey(
        ResultSet, on_delete=models.SET_NULL, null=True, blank=True
    )
    payload = models.JSONField(default=dict)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(
                fields=["project", "-created"], name="research_event_proj_created"
            )
        ]

    def __str__(self) -> str:
        return self.verb


class SetItem(models.Model):
    class Disposition(models.TextChoices):
        UNREVIEWED = "u", "Unreviewed"
        RELEVANT = "r", "Relevant"
        IGNORED = "i", "Ignored"

    class AddedVia(models.TextChoices):
        QUERY = "query", "Query"
        LENS = "lens", "Lens"
        NEW_MATCHES = "new_matches", "New matches"
        MANUAL = "manual", "Manual"
        IMPORT = "import", "Import"

    id = models.BigAutoField(primary_key=True)
    result_set = models.ForeignKey(
        ResultSet, on_delete=models.CASCADE, related_name="items", db_index=False
    )
    page = models.ForeignKey(
        "meetings.MeetingPage",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    rank = models.FloatField(null=True, blank=True)
    meeting_date = models.DateField(null=True, blank=True)
    municipality_id = models.UUIDField(null=True, blank=True)
    state = models.CharField(max_length=64, blank=True)
    document_type = models.CharField(max_length=32, blank=True)
    disposition = models.CharField(
        max_length=1, choices=Disposition, default=Disposition.UNREVIEWED
    )
    previous_disposition = models.CharField(max_length=1, blank=True, default="")
    disposition_by_rule = models.ForeignKey(
        IgnoreRule, on_delete=models.SET_NULL, null=True, blank=True
    )
    ignore_reason = models.CharField(max_length=500, blank=True)
    last_event = models.ForeignKey(
        AuditEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_constraint=False,
    )
    added_via = models.CharField(
        max_length=16, choices=AddedVia, default=AddedVia.QUERY
    )
    added_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-rank", "page"]
        constraints = [
            models.UniqueConstraint(
                fields=["result_set", "page"], name="research_setitem_uniq_item"
            )
        ]
        indexes = [
            models.Index(
                "result_set",
                "disposition",
                models.F("rank").desc(),
                "page",
                name="research_item_set_disp_rank",
            ),
            models.Index(
                fields=["result_set", "meeting_date", "page"],
                name="research_item_set_date_page",
            ),
            models.Index(
                fields=["result_set", "municipality_id"],
                name="research_item_set_muni_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.result_set_id}:{self.page_id}"

    @property
    def can_undo(self) -> bool:
        return bool(
            self.last_event_id
            and self.last_event
            and self.last_event.verb == "items.disposed"
        )


class Coding(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="codings"
    )
    code = models.ForeignKey(Code, on_delete=models.CASCADE, related_name="codings")
    page = models.ForeignKey(
        "meetings.MeetingPage",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    span_start = models.PositiveIntegerField(null=True, blank=True)
    span_end = models.PositiveIntegerField(null=True, blank=True)
    quote = models.TextField(blank=True)
    page_hash = models.CharField(max_length=64, blank=True)
    is_exemplar = models.BooleanField(default=False)
    coder = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )

    # Set transiently by the page-coding view when the page text no longer
    # matches the text captured at coding time; not persisted.
    changed: bool = False

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(span_start__isnull=True, span_end__isnull=True)
                    | models.Q(
                        span_start__isnull=False,
                        span_end__isnull=False,
                        span_start__lt=models.F("span_end"),
                    )
                ),
                name="research_coding_valid_span",
            ),
            models.UniqueConstraint(
                fields=["code", "page"],
                condition=models.Q(span_start__isnull=True, span_end__isnull=True),
                name="research_coding_uniq_page",
            ),
            models.UniqueConstraint(
                fields=["code", "page", "span_start", "span_end"],
                name="research_coding_uniq_span",
            ),
        ]
        indexes = [
            models.Index(
                fields=["project", "page"], name="research_coding_proj_page_idx"
            ),
            models.Index(fields=["code", "page"], name="research_coding_code_page_idx"),
        ]


class Memo(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="memos")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    body = models.TextField()
    result_set = models.ForeignKey(
        ResultSet,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="memos",
    )
    code = models.ForeignKey(
        Code,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="memos",
    )
    page = models.ForeignKey(
        "meetings.MeetingPage",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["-created"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        result_set__isnull=True, code__isnull=True, page__isnull=True
                    )
                    | models.Q(
                        result_set__isnull=False, code__isnull=True, page__isnull=True
                    )
                    | models.Q(
                        result_set__isnull=True, code__isnull=False, page__isnull=True
                    )
                    | models.Q(
                        result_set__isnull=True, code__isnull=True, page__isnull=False
                    )
                ),
                name="research_memo_at_most_one_target",
            )
        ]
