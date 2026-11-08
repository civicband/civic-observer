from django.contrib.auth import get_user_model

from .materialize import materialize_result_set


def _resolve_actor(actor_id):
    if not actor_id:
        return None
    return get_user_model().objects.filter(pk=actor_id).first()


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
    from .models import IgnoreRule
    from .rules import apply_rule_to_project

    try:
        rule = IgnoreRule.objects.get(pk=rule_id)
        apply_rule_to_project(rule, actor=_resolve_actor(actor_id))
    except IgnoreRule.DoesNotExist:
        return


def revert_rule_task(rule_id, actor_id=None) -> None:
    from .models import IgnoreRule
    from .rules import revert_rule

    try:
        rule = IgnoreRule.objects.get(pk=rule_id)
        revert_rule(rule, actor=_resolve_actor(actor_id))
    except IgnoreRule.DoesNotExist:
        return


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
