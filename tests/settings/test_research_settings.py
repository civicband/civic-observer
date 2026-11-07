from django.conf import settings


def test_research_queue_is_configured():
    assert settings.RESEARCH_QUEUE == "research"
    assert "research" in settings.RQ_QUEUES


def test_research_limits_have_initial_values():
    assert settings.RESEARCH_SET_MAX_ITEMS == 500_000
    assert settings.RESEARCH_INLINE_SET_MAX_ITEMS == 1_000
    assert settings.RESEARCH_JOB_TIMEOUT > 0


def test_test_queue_runs_synchronously():
    for queue in ("default", "research"):
        assert settings.RQ_QUEUES[queue]["ASYNC"] is False
