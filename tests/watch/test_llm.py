"""Tests for the optional LLM place pass and its extraction trigger."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.factories import MentionFactory, SubredditFactory, ThreadFactory
from watch.extraction import extract_thread_places
from watch.llm import LLMPlace, classify_with_llm
from watch.models import PlaceMention, ThreadPlace


def _response(content: dict | str) -> SimpleNamespace:
    """Build the small response surface the client uses."""
    payload = content if isinstance(content, str) else json.dumps(content)
    return SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"choices": [{"message": {"content": payload}}]},
    )


def _enable(settings) -> None:
    settings.WATCH_LLM_ENABLED = True
    settings.WATCH_LLM_BASE_URL = "https://llm.example/v1"
    settings.WATCH_LLM_MODEL = "small"


@pytest.mark.django_db
def test_llm_disabled_makes_no_request(settings, monkeypatch):
    settings.WATCH_LLM_ENABLED = False
    monkeypatch.setattr("httpx.post", lambda *a, **k: pytest.fail("must not call"))
    assert classify_with_llm(ThreadFactory()) is None


@pytest.mark.django_db
def test_llm_parses_json(settings, monkeypatch):
    _enable(settings)
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _response(
            {
                "places": [{"name": "Chico", "state": "CA", "role": "subject"}],
                "is_us": True,
                "topic": "budget",
            }
        ),
    )

    result = classify_with_llm(ThreadFactory(title="Budget"))

    assert result is not None
    assert result.places == [LLMPlace(name="Chico", state="CA", role="subject")]
    assert result.is_us is True
    assert result.topic == "budget"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("base_url", "model"),
    [("", "small"), ("https://llm.example/v1", "")],
)
def test_llm_blank_configuration_makes_no_request(
    settings, monkeypatch, base_url, model
):
    settings.WATCH_LLM_ENABLED = True
    settings.WATCH_LLM_BASE_URL = base_url
    settings.WATCH_LLM_MODEL = model
    monkeypatch.setattr("httpx.post", lambda *a, **k: pytest.fail("must not call"))
    assert classify_with_llm(ThreadFactory()) is None


@pytest.mark.django_db
def test_llm_request_shape_and_api_key(settings, monkeypatch):
    _enable(settings)
    settings.WATCH_LLM_API_KEY = "secret"
    settings.WATCH_LLM_TIMEOUT = 7
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _response({"places": [], "is_us": True, "topic": ""})

    monkeypatch.setattr("httpx.post", fake_post)

    classify_with_llm(ThreadFactory(title="Budget"))

    assert captured["url"] == "https://llm.example/v1/chat/completions"
    assert captured["timeout"] == 7
    assert captured["headers"]["Authorization"] == "Bearer secret"
    body = captured["json"]
    assert body["model"] == "small"
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    assert "places" in body["messages"][0]["content"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"places": [], "is_us": "yes", "topic": ""}',
        '{"places": [], "is_us": true}',
        '{"places": {}}',
        '{"places": [{"name": 1, "state": "CA", "role": "subject"}], "is_us": true, "topic": ""}',
        '{"places": [{"name": "Chico", "state": "CA", "role": "other"}], "is_us": true, "topic": ""}',
        "[]",
    ],
)
def test_llm_rejects_malformed_response(settings, monkeypatch, content):
    _enable(settings)
    monkeypatch.setattr("httpx.post", lambda *a, **k: _response(content))
    assert classify_with_llm(ThreadFactory()) is None


@pytest.mark.django_db
def test_llm_returns_none_on_http_error(settings, monkeypatch):
    _enable(settings)

    def raise_error(*args, **kwargs):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr("httpx.post", raise_error)
    assert classify_with_llm(ThreadFactory(title="sensitive snippet")) is None


@pytest.mark.django_db
def test_llm_returns_none_when_raise_for_status_fails(settings, monkeypatch):
    _enable(settings)

    def raise_status():
        raise RuntimeError("500 server error")

    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: SimpleNamespace(
            raise_for_status=raise_status,
            json=lambda: {"choices": []},
        ),
    )
    assert classify_with_llm(ThreadFactory(title="Budget")) is None


@pytest.mark.django_db
def test_llm_error_log_omits_thread_text(settings, monkeypatch, caplog):
    _enable(settings)

    def raise_error(*args, **kwargs):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr("httpx.post", raise_error)

    with caplog.at_level("WARNING"):
        assert classify_with_llm(ThreadFactory(title="SECRET-SNIPPET")) is None

    assert "LLM place classification failed" in caplog.text
    assert "SECRET-SNIPPET" not in caplog.text


@pytest.mark.django_db
def test_llm_omits_authorization_header_without_key(settings, monkeypatch):
    _enable(settings)
    settings.WATCH_LLM_API_KEY = ""
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return _response({"places": [], "is_us": True, "topic": ""})

    monkeypatch.setattr("httpx.post", fake_post)

    classify_with_llm(ThreadFactory(title="Budget"))

    assert "Authorization" not in captured["headers"]


@pytest.mark.django_db
def test_llm_normalizes_and_validates_state(settings, monkeypatch):
    _enable(settings)
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _response(
            {
                "places": [
                    {"name": "Chico", "state": "ca", "role": "subject"},
                    {"name": "Hope", "state": "California", "role": "mentioned"},
                    {"name": "Mobile", "state": "c1", "role": "mentioned"},
                ],
                "is_us": True,
                "topic": "",
            }
        ),
    )

    result = classify_with_llm(ThreadFactory(title="Budget"))

    assert result is not None
    assert [(place.name, place.state) for place in result.places] == [
        ("Chico", "CA"),
        ("Hope", ""),
        ("Mobile", ""),
    ]


@pytest.mark.django_db
def test_llm_caps_places_at_twenty(settings, monkeypatch):
    _enable(settings)
    places = [
        {"name": f"Place {index}", "state": "CA", "role": "mentioned"}
        for index in range(25)
    ]
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _response({"places": places, "is_us": True, "topic": ""}),
    )

    result = classify_with_llm(ThreadFactory(title="Budget"))

    assert result is not None
    assert len(result.places) == 20


@pytest.mark.django_db
def test_general_subreddit_adds_llm_subject(settings, monkeypatch, extraction_places):
    _enable(settings)
    subreddit = SubredditFactory(kind="general")
    thread = ThreadFactory(subreddit=subreddit)
    mention = MentionFactory(thread=thread, kind="post", snippet_text="Hope is nearby.")
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _response(
            {
                "places": [{"name": "Hope", "state": "AR", "role": "subject"}],
                "is_us": True,
                "topic": "vote",
            }
        ),
    )

    extract_thread_places(thread)

    place_mention = PlaceMention.objects.get(
        mention=mention, place=extraction_places["hope"]
    )
    assert (
        place_mention.method,
        place_mention.confidence,
        place_mention.is_subject,
    ) == ("llm", "medium", True)
    assert (
        ThreadPlace.objects.get(thread=thread, place=extraction_places["hope"]).role
        == "subject"
    )


@pytest.mark.django_db
def test_llm_mentioned_role_is_honored(settings, monkeypatch, extraction_places):
    _enable(settings)
    thread = ThreadFactory(subreddit=SubredditFactory(kind="region"))
    mention = MentionFactory(thread=thread, kind="post", snippet_text="Hope is nearby.")
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _response(
            {
                "places": [{"name": "Hope", "state": "AR", "role": "mentioned"}],
                "is_us": True,
                "topic": "",
            }
        ),
    )

    extract_thread_places(thread)

    assert not PlaceMention.objects.get(
        mention=mention, place=extraction_places["hope"]
    ).is_subject
    assert (
        ThreadPlace.objects.get(thread=thread, place=extraction_places["hope"]).role
        == "mentioned"
    )


@pytest.mark.django_db
def test_llm_clears_stale_rows_before_adding(settings, monkeypatch, extraction_places):
    _enable(settings)
    thread = ThreadFactory(subreddit=SubredditFactory(kind="general"))
    mention = MentionFactory(thread=thread, kind="post", snippet_text="Hope is nearby.")
    PlaceMention.objects.create(
        mention=mention,
        place=extraction_places["beverly_hills"],
        matched_text="Beverly Hills",
        method=PlaceMention.Method.LLM,
        confidence=PlaceMention.Confidence.MEDIUM,
        is_subject=True,
    )
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _response(
            {
                "places": [{"name": "Hope", "state": "AR", "role": "subject"}],
                "is_us": True,
                "topic": "",
            }
        ),
    )

    extract_thread_places(thread)

    assert not PlaceMention.objects.filter(
        mention=mention, place=extraction_places["beverly_hills"]
    ).exists()
    assert PlaceMention.objects.filter(
        mention=mention, place=extraction_places["hope"], method="llm"
    ).exists()


@pytest.mark.django_db
def test_llm_unresolved_names_are_ignored(settings, monkeypatch, extraction_places):
    _enable(settings)
    thread = ThreadFactory(subreddit=SubredditFactory(kind="general"))
    MentionFactory(thread=thread, kind="post", snippet_text="Zzyzx is nearby.")
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _response(
            {
                "places": [{"name": "Zzyzx", "state": "CA", "role": "subject"}],
                "is_us": True,
                "topic": "",
            }
        ),
    )

    extract_thread_places(thread)

    assert not PlaceMention.objects.filter(
        mention__thread=thread, method="llm"
    ).exists()
    assert not ThreadPlace.objects.filter(thread=thread).exists()


@pytest.mark.django_db
def test_llm_hallucinated_name_is_dropped(settings, monkeypatch, extraction_places):
    _enable(settings)
    thread = ThreadFactory(subreddit=SubredditFactory(kind="general"))
    MentionFactory(thread=thread, kind="post", snippet_text="Hope is nearby.")
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _response(
            {
                "places": [{"name": "Beverly Hills", "state": "CA", "role": "subject"}],
                "is_us": True,
                "topic": "",
            }
        ),
    )

    extract_thread_places(thread)

    assert not PlaceMention.objects.filter(
        mention__thread=thread, method="llm"
    ).exists()
    assert not ThreadPlace.objects.filter(thread=thread).exists()


@pytest.mark.django_db
def test_llm_grounding_is_case_insensitive(settings, monkeypatch, extraction_places):
    _enable(settings)
    thread = ThreadFactory(subreddit=SubredditFactory(kind="general"))
    MentionFactory(thread=thread, kind="post", snippet_text="the chico vote passed.")
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _response(
            {
                "places": [{"name": "Chico", "state": "CA", "role": "subject"}],
                "is_us": True,
                "topic": "",
            }
        ),
    )

    extract_thread_places(thread)

    assert PlaceMention.objects.filter(
        mention__thread=thread,
        place=extraction_places["chico"],
        method="llm",
    ).exists()


@pytest.mark.django_db
def test_llm_grounding_ignores_text_beyond_prompt_truncation(
    settings, monkeypatch, extraction_places
):
    _enable(settings)
    thread = ThreadFactory(subreddit=SubredditFactory(kind="general"), title="Budget")
    MentionFactory(thread=thread, kind="post", snippet_text="No place here.")
    MentionFactory(
        thread=thread,
        kind="comment",
        snippet_text=("zoning " * 1200) + "Beverly Hills is nearby.",
    )
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(args)
        return _response(
            {
                "places": [{"name": "Beverly Hills", "state": "CA", "role": "subject"}],
                "is_us": True,
                "topic": "",
            }
        )

    monkeypatch.setattr("httpx.post", fake_post)

    extract_thread_places(thread)

    assert len(calls) == 1
    assert not PlaceMention.objects.filter(
        mention__thread=thread, method="llm"
    ).exists()


@pytest.mark.django_db
def test_llm_conflicting_state_uses_subreddit_state(
    settings, monkeypatch, extraction_places
):
    _enable(settings)
    subreddit = SubredditFactory(kind="general", state="IL")
    thread = ThreadFactory(subreddit=subreddit)
    MentionFactory(thread=thread, kind="post", snippet_text="springfield is nearby.")
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _response(
            {
                "places": [{"name": "Springfield", "state": "MO", "role": "subject"}],
                "is_us": True,
                "topic": "",
            }
        ),
    )

    extract_thread_places(thread)

    place_mention = PlaceMention.objects.get(mention__thread=thread, method="llm")
    assert (place_mention.place.name, place_mention.place.state) == (
        "Springfield",
        "IL",
    )


@pytest.mark.django_db
def test_llm_invalid_state_uses_subreddit_state(
    settings, monkeypatch, extraction_places
):
    _enable(settings)
    subreddit = SubredditFactory(kind="general", state="IL")
    thread = ThreadFactory(subreddit=subreddit)
    MentionFactory(thread=thread, kind="post", snippet_text="springfield is nearby.")
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _response(
            {
                "places": [
                    {"name": "Springfield", "state": "Illinois", "role": "subject"}
                ],
                "is_us": True,
                "topic": "",
            }
        ),
    )

    extract_thread_places(thread)

    place_mention = PlaceMention.objects.get(mention__thread=thread, method="llm")
    assert (place_mention.place.name, place_mention.place.state) == (
        "Springfield",
        "IL",
    )


@pytest.mark.django_db
def test_llm_valid_state_resolves_when_subreddit_has_none(
    settings, monkeypatch, extraction_places
):
    _enable(settings)
    thread = ThreadFactory(subreddit=SubredditFactory(kind="general"))
    MentionFactory(thread=thread, kind="post", snippet_text="springfield is nearby.")
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _response(
            {
                "places": [{"name": "Springfield", "state": "IL", "role": "subject"}],
                "is_us": True,
                "topic": "",
            }
        ),
    )

    extract_thread_places(thread)

    place_mention = PlaceMention.objects.get(mention__thread=thread, method="llm")
    assert (place_mention.place.name, place_mention.place.state) == (
        "Springfield",
        "IL",
    )


@pytest.mark.django_db
def test_llm_invalid_state_without_subreddit_state_is_ignored(
    settings, monkeypatch, extraction_places
):
    _enable(settings)
    thread = ThreadFactory(subreddit=SubredditFactory(kind="general"))
    MentionFactory(thread=thread, kind="post", snippet_text="springfield is nearby.")
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _response(
            {
                "places": [
                    {"name": "Springfield", "state": "Illinois", "role": "subject"}
                ],
                "is_us": True,
                "topic": "",
            }
        ),
    )

    extract_thread_places(thread)

    assert not PlaceMention.objects.filter(
        mention__thread=thread, method="llm"
    ).exists()


@pytest.mark.django_db
def test_llm_trigger_skips_thread_without_mentions(
    settings, monkeypatch, extraction_places
):
    _enable(settings)
    thread = ThreadFactory(subreddit=SubredditFactory(kind="general"))
    monkeypatch.setattr("httpx.post", lambda *a, **k: pytest.fail("must not call"))

    extract_thread_places(thread)


@pytest.mark.django_db
def test_llm_trigger_skips_agreeing_subreddit(settings, monkeypatch, extraction_places):
    _enable(settings)
    subreddit = SubredditFactory(kind="place", place=extraction_places["chico"])
    thread = ThreadFactory(subreddit=subreddit)
    MentionFactory(thread=thread, kind="post", snippet_text="Chico city council voted.")
    monkeypatch.setattr("httpx.post", lambda *a, **k: pytest.fail("must not call"))

    extract_thread_places(thread)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["region", "state"])
def test_llm_trigger_runs_for_region_and_state(
    settings, monkeypatch, extraction_places, kind
):
    _enable(settings)
    thread = ThreadFactory(subreddit=SubredditFactory(kind=kind))
    MentionFactory(thread=thread, kind="post", snippet_text="The vote passed.")
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(args)
        return _response({"places": [], "is_us": True, "topic": ""})

    monkeypatch.setattr("httpx.post", fake_post)

    extract_thread_places(thread)

    assert len(calls) == 1


@pytest.mark.django_db
def test_llm_trigger_runs_when_deterministic_disagrees(
    settings, monkeypatch, extraction_places
):
    _enable(settings)
    subreddit = SubredditFactory(kind="place", place=extraction_places["chico"])
    thread = ThreadFactory(subreddit=subreddit)
    MentionFactory(
        thread=thread,
        kind="post",
        snippet_text="Beverly Hills city council voted.",
    )
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(args)
        return _response({"places": [], "is_us": True, "topic": ""})

    monkeypatch.setattr("httpx.post", fake_post)

    extract_thread_places(thread)

    assert len(calls) == 1


@pytest.mark.django_db
def test_llm_trigger_runs_for_ambiguous_match(settings, monkeypatch, extraction_places):
    _enable(settings)
    subreddit = SubredditFactory(kind="place", place=extraction_places["chico"])
    thread = ThreadFactory(subreddit=subreddit)
    MentionFactory(thread=thread, kind="post", snippet_text="Springfield is nearby.")
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(args)
        return _response({"places": [], "is_us": True, "topic": ""})

    monkeypatch.setattr("httpx.post", fake_post)

    extract_thread_places(thread)

    assert len(calls) == 1


@pytest.mark.django_db
def test_llm_trigger_runs_for_ambiguous_thread_title(
    settings, monkeypatch, extraction_places
):
    _enable(settings)
    subreddit = SubredditFactory(kind="place", place=extraction_places["chico"])
    thread = ThreadFactory(subreddit=subreddit, title="Springfield ordinance")
    MentionFactory(thread=thread, kind="comment", snippet_text="The vote passed.")
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(args)
        return _response({"places": [], "is_us": True, "topic": ""})

    monkeypatch.setattr("httpx.post", fake_post)

    extract_thread_places(thread)

    assert len(calls) == 1


@pytest.mark.django_db
def test_llm_disabled_pipeline_makes_no_request(
    settings, monkeypatch, extraction_places
):
    settings.WATCH_LLM_ENABLED = False
    thread = ThreadFactory(subreddit=SubredditFactory(kind="general"))
    MentionFactory(thread=thread, kind="post", snippet_text="Hope is nearby.")
    monkeypatch.setattr("httpx.post", lambda *a, **k: pytest.fail("must not call"))

    extract_thread_places(thread)

    assert not PlaceMention.objects.filter(
        mention__thread=thread, method="llm"
    ).exists()


@pytest.mark.django_db
def test_llm_no_call_for_unresolvable_non_ambiguous_names(
    settings, monkeypatch, extraction_places
):
    _enable(settings)
    subreddit = SubredditFactory(kind="place", place=extraction_places["chico"])
    thread = ThreadFactory(subreddit=subreddit)
    MentionFactory(thread=thread, kind="post", snippet_text="Zzyzx is nearby.")
    monkeypatch.setattr("httpx.post", lambda *a, **k: pytest.fail("must not call"))

    extract_thread_places(thread)


@pytest.mark.django_db
def test_llm_empty_result_keeps_deterministic_extraction(
    settings, monkeypatch, extraction_places
):
    _enable(settings)
    thread = ThreadFactory(subreddit=SubredditFactory(kind="general"))
    MentionFactory(thread=thread, kind="post", snippet_text="Chico city council voted.")
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _response({"places": [], "is_us": True, "topic": ""}),
    )

    extract_thread_places(thread)

    assert PlaceMention.objects.filter(
        mention__thread=thread, method="body_pattern"
    ).exists()
    assert (
        ThreadPlace.objects.get(thread=thread, place=extraction_places["chico"]).role
        == "subject"
    )


@pytest.mark.django_db
def test_llm_failure_keeps_deterministic_extraction(
    settings, monkeypatch, extraction_places
):
    _enable(settings)
    thread = ThreadFactory(subreddit=SubredditFactory(kind="general"))
    MentionFactory(thread=thread, kind="post", snippet_text="Chico city council voted.")

    def raise_error(*args, **kwargs):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr("httpx.post", raise_error)

    extract_thread_places(thread)

    assert PlaceMention.objects.filter(
        mention__thread=thread, method="body_pattern"
    ).exists()
    assert (
        ThreadPlace.objects.get(thread=thread, place=extraction_places["chico"]).role
        == "subject"
    )
