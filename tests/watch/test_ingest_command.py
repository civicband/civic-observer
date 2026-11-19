import logging
from io import StringIO
from pathlib import Path

import httpx
import pytest
from django.core.management import CommandError, call_command

from watch.models import IngestRun, Mention

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.django_db
def test_command_ingests_file_without_lock(monkeypatch):
    import django_rq

    def fail_if_called(*args, **kwargs):
        raise AssertionError("--no-lock must not connect to Redis")

    monkeypatch.setattr(django_rq, "get_connection", fail_if_called)
    call_command(
        "ingest_f5bot", "--file", str(FIXTURES / "f5bot_page_1.json"), "--no-lock"
    )
    assert Mention.objects.count() == 50
    run = IngestRun.objects.get()
    assert run.source == "file:f5bot_page_1.json"
    assert (run.pages, run.seen, run.new, run.skipped) == (1, 0, 50, 0)
    assert run.finished_at is not None
    assert run.error == ""


@pytest.mark.django_db
def test_command_skips_when_lock_held(monkeypatch):
    from watch import ingest

    monkeypatch.setattr(ingest, "acquire_ingest_lock", lambda: None)
    output = StringIO()
    call_command(
        "ingest_f5bot", "--file", str(FIXTURES / "f5bot_page_1.json"), stderr=output
    )
    assert IngestRun.objects.count() == 0
    assert Mention.objects.count() == 0
    assert "lock" in output.getvalue().lower()


@pytest.mark.django_db
def test_multiple_files_record_independent_runs():
    call_command(
        "ingest_f5bot",
        "--file",
        str(FIXTURES / "f5bot_page_1.json"),
        "--file",
        str(FIXTURES / "f5bot_page_2.json"),
        "--no-lock",
    )
    runs = {run.source: run for run in IngestRun.objects.all()}
    assert set(runs) == {"file:f5bot_page_1.json", "file:f5bot_page_2.json"}
    assert [(runs[key].pages, runs[key].new) for key in sorted(runs)] == [
        (1, 50),
        (1, 38),
    ]
    assert Mention.objects.count() == 88


@pytest.mark.django_db
def test_missing_url_is_an_error(settings):
    settings.F5BOT_FEED_URL = ""
    with pytest.raises(CommandError, match="F5BOT_FEED_URL is not set"):
        call_command("ingest_f5bot", "--no-lock")
    assert not IngestRun.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("override", [False, True])
def test_feed_option_and_page_limit_hide_url(settings, monkeypatch, override):
    settings.F5BOT_FEED_URL = "https://example.test/feed?token=SETTINGS_SECRET"
    url = (
        "https://example.test/feed?token=OPTION_SECRET"
        if override
        else settings.F5BOT_FEED_URL
    )
    requested = []

    def get(client, request_url):
        requested.append(request_url)
        return httpx.Response(
            200,
            json={
                "items": [{"id": "a", "url": "https://redd.it/abc123"}],
                "next_url": "https://example.test/next?token=SECRET",
            },
            request=httpx.Request("GET", request_url),
        )

    monkeypatch.setattr(httpx.Client, "get", get)
    output, errors = StringIO(), StringIO()
    args = ["--no-lock", "--max-pages", "1"]
    if override:
        args += ["--feed-url", url]
    call_command("ingest_f5bot", *args, stdout=output, stderr=errors)
    assert requested == [url]
    run = IngestRun.objects.get()
    assert (run.source, run.pages, run.new, run.error) == ("feed", 1, 1, "")
    assert "SECRET" not in output.getvalue() + errors.getvalue() + run.source


@pytest.mark.django_db
def test_failed_file_is_finished_and_recorded(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("invalid JSON")
    with pytest.raises(CommandError):
        call_command("ingest_f5bot", "--file", str(path), "--no-lock")
    run = IngestRun.objects.get()
    assert run.source == "file:broken.json"
    assert run.finished_at is not None
    assert run.pages == 0
    assert "JSONDecodeError" in run.error


@pytest.mark.django_db
def test_file_failure_does_not_prevent_other_file_runs(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("invalid JSON")
    with pytest.raises(CommandError):
        call_command(
            "ingest_f5bot",
            "--file",
            str(path),
            "--file",
            str(FIXTURES / "f5bot_page_1.json"),
            "--no-lock",
        )
    assert set(IngestRun.objects.values_list("source", flat=True)) == {
        "file:broken.json",
        "file:f5bot_page_1.json",
    }
    assert Mention.objects.count() == 50


@pytest.mark.django_db
def test_failed_feed_does_not_expose_url(settings, monkeypatch):
    settings.F5BOT_FEED_URL = "https://example.test/feed?token=SECRET"

    def get(client, url):
        raise httpx.ConnectError(f"could not fetch {url}")

    monkeypatch.setattr(httpx.Client, "get", get)
    output, errors = StringIO(), StringIO()
    with pytest.raises(CommandError) as exc:
        call_command("ingest_f5bot", "--no-lock", stdout=output, stderr=errors)
    run = IngestRun.objects.get()
    assert run.finished_at is not None
    assert "ConnectError" in run.error
    assert (
        "SECRET"
        not in str(exc.value)
        + output.getvalue()
        + errors.getvalue()
        + run.source
        + run.error
    )


@pytest.mark.django_db
def test_http_client_does_not_log_feed_url(settings, monkeypatch, caplog):
    settings.F5BOT_FEED_URL = "https://example.test/feed?token=LOG_SECRET"

    def send(transport, request):
        logging.getLogger("httpcore.http11").debug("request %s", request.url)
        return httpx.Response(200, json={"items": []}, request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", send)
    with caplog.at_level(logging.DEBUG):
        call_command("ingest_f5bot", "--no-lock")
    assert IngestRun.objects.get().pages == 1
    assert "LOG_SECRET" not in caplog.text


@pytest.mark.django_db
def test_real_redis_lock_blocks_overlapping_command_and_is_released(settings):
    import django_rq

    from watch.ingest import acquire_ingest_lock

    settings.WATCH_INGEST_LOCK_SECONDS = 73
    output = StringIO()
    with acquire_ingest_lock() as acquired:
        assert acquired
        assert 70000 < django_rq.get_connection("default").pttl("watch:ingest") <= 73000
        call_command(
            "ingest_f5bot", "--file", str(FIXTURES / "f5bot_page_1.json"), stderr=output
        )
        assert not IngestRun.objects.exists()
        assert "lock" in output.getvalue().lower()
    call_command("ingest_f5bot", "--file", str(FIXTURES / "f5bot_page_1.json"))
    assert IngestRun.objects.count() == 1


@pytest.mark.django_db
def test_lock_is_released_after_file_failure(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("invalid JSON")
    with pytest.raises(CommandError):
        call_command("ingest_f5bot", "--file", str(path))
    call_command("ingest_f5bot", "--file", str(FIXTURES / "f5bot_page_1.json"))
    assert IngestRun.objects.count() == 2
    assert Mention.objects.count() == 50
