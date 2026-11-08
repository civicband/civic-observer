from .materialize import materialize_result_set


def materialize_result_set_task(result_set_id) -> None:
    materialize_result_set(result_set_id)


def materialize_lens_set_task(result_set_id) -> None:
    from .freeze import materialize_lens_set

    materialize_lens_set(result_set_id)
