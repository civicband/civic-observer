from .materialize import materialize_result_set


def materialize_result_set_task(result_set_id) -> None:
    materialize_result_set(result_set_id)
