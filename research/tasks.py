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
    except IgnoreRule.DoesNotExist:
        return
    apply_rule_to_project(rule, actor=_resolve_actor(actor_id))


def revert_rule_task(rule_id, actor_id=None) -> None:
    from .models import IgnoreRule
    from .rules import revert_rule

    try:
        rule = IgnoreRule.objects.get(pk=rule_id)
    except IgnoreRule.DoesNotExist:
        return
    revert_rule(rule, actor=_resolve_actor(actor_id))
