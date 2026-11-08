from .materialize import materialize_result_set


def materialize_result_set_task(result_set_id) -> None:
    materialize_result_set(result_set_id)


def materialize_lens_set_task(result_set_id) -> None:
    from .freeze import materialize_lens_set

    materialize_lens_set(result_set_id)


def check_new_matches_task(result_set_id) -> None:
    from .models import ResultSet
    from .new_matches import check_for_new_matches

    check_for_new_matches(ResultSet.objects.get(pk=result_set_id))


def apply_rule_to_project_task(rule_id) -> None:
    from .models import IgnoreRule
    from .rules import apply_rule_to_project

    apply_rule_to_project(IgnoreRule.objects.get(pk=rule_id))


def revert_rule_task(rule_id) -> None:
    from .models import IgnoreRule
    from .rules import revert_rule

    revert_rule(IgnoreRule.objects.get(pk=rule_id))
