from django.contrib.auth import get_user_model

from .materialize import materialize_result_set


def _resolve_actor(actor_id):
    if not actor_id:
        return None
    return get_user_model().objects.filter(pk=actor_id).first()


def _current_job_id() -> str:
    try:
        import rq

        job = rq.get_current_job()
    except Exception:
        return ""
    return str(job.id) if job is not None else ""


def _update_rule_job(rule_id, job_id: str, **fields) -> None:
    from django.db.models import Q

    from .models import IgnoreRule

    rules = IgnoreRule.objects.filter(pk=rule_id)
    if job_id:
        rules = rules.filter(Q(last_job_id=job_id) | Q(last_job_id=""))
    rules.update(**fields)


def _run_rule_job(rule_id, action: str, func, actor=None) -> None:
    from django.utils import timezone

    from .models import IgnoreRule

    job_id = _current_job_id()
    rule = IgnoreRule.objects.filter(pk=rule_id).first()
    if rule is None:
        return
    if job_id and rule.last_job_id and rule.last_job_id != job_id:
        return

    _update_rule_job(
        rule_id,
        job_id,
        last_job_action=action,
        last_job_status=IgnoreRule.JobStatus.RUNNING,
        last_job_error="",
    )
    try:
        event = func(rule, actor=actor)
        count = event.payload.get("count", 0) if event else 0
    except Exception as exc:
        _update_rule_job(
            rule_id,
            job_id,
            last_job_status=IgnoreRule.JobStatus.FAILED,
            last_job_error=str(exc),
            last_job_finished_at=timezone.now(),
        )
        raise

    _update_rule_job(
        rule_id,
        job_id,
        last_job_status=IgnoreRule.JobStatus.SUCCEEDED,
        last_job_error="",
        last_job_count=count,
        last_job_finished_at=timezone.now(),
    )


def materialize_result_set_task(result_set_id) -> None:
    materialize_result_set(result_set_id)


def materialize_lens_set_task(result_set_id) -> None:
    from .freeze import materialize_lens_set

    materialize_lens_set(result_set_id)


def check_new_matches_task(result_set_id, actor_id=None) -> None:
    from .models import ResultSet
    from .new_matches import check_for_new_matches

    check_for_new_matches(
        ResultSet.objects.get(pk=result_set_id), actor=_resolve_actor(actor_id)
    )


def apply_rule_to_project_task(rule_id, actor_id=None) -> None:
    from .rules import apply_rule_to_project

    _run_rule_job(
        rule_id,
        "apply",
        apply_rule_to_project,
        actor=_resolve_actor(actor_id),
    )


def revert_rule_task(rule_id, actor_id=None) -> None:
    from .rules import revert_rule

    _run_rule_job(
        rule_id,
        "revert",
        revert_rule,
        actor=_resolve_actor(actor_id),
    )


def export_lens_task(result_set_id, lens_canonical, user_id=None) -> str:
    import uuid
    from pathlib import Path

    from django.conf import settings

    from .export import export_zip
    from .lens import Lens
    from .models import ResultSet

    result_set = ResultSet.objects.select_related("project").get(pk=result_set_id)
    lens = Lens.from_canonical(lens_canonical)
    payload = export_zip(result_set.project, result_set, lens)

    job_id = str(uuid.uuid4())
    try:
        import rq
        from rq.exceptions import NoSuchJobError

        job = rq.get_current_job()
        if job is not None:
            job_id = job.id
    except (ImportError, NoSuchJobError):
        pass

    directory = Path(settings.MEDIA_ROOT) / "research_exports"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{job_id}.zip"
    path.write_bytes(payload)
    return str(path)
