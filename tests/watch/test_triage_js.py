"""Static checks for the triage keyboard script and staff account navigation."""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from tests.factories import ThreadFactory

SCRIPT = Path(settings.BASE_DIR) / "frontend/js/watch-triage.js"
TRIAGE_CARD = Path(settings.BASE_DIR) / "templates/watch/partials/triage_card.html"


def test_triage_script_has_shortcuts_and_copy():
    text = SCRIPT.read_text()
    for key in ["j", "k", "r", "d", "s", "b", "p", "m", "x", "e", "c"]:
        assert f"{key}:" in text
    assert 'Alpine.data("triageKeyboard"' in text
    assert "navigator.clipboard.writeText" in text
    assert '[data-action="copy-citation"]' in text


def test_triage_script_ignores_form_controls_and_ime_repeats():
    text = SCRIPT.read_text()
    for tag in ["INPUT", "TEXTAREA", "SELECT"]:
        assert tag in text
    assert "contenteditable" in text
    assert "isTyping(event.target)" in text
    assert "event.repeat" in text
    assert "event.isComposing" in text


def test_triage_script_has_clipboard_fallback():
    text = SCRIPT.read_text()
    assert 'document.execCommand("copy")' in text


def test_triage_script_guards_against_racing_requests():
    text = SCRIPT.read_text()
    assert "busy: false" in text
    assert "self.busy = false;" in text
    assert "this.busy = true;" in text
    for event_name in [
        "htmx:afterRequest",
        "htmx:responseError",
        "htmx:sendError",
    ]:
        assert event_name in text


def test_triage_nav_links_have_structural_actions():
    text = TRIAGE_CARD.read_text()
    assert 'data-action="previous"' in text
    assert 'data-action="next"' in text


@pytest.mark.django_db
def test_rendered_triage_nav_links_carry_data_actions(admin_client):
    newest = ThreadFactory(last_seen=timezone.now())
    middle = ThreadFactory(last_seen=timezone.now() - timedelta(minutes=1))
    oldest = ThreadFactory(last_seen=timezone.now() - timedelta(minutes=2))

    content = admin_client.get(
        reverse("watch:triage"), {"thread": middle.pk}
    ).content.decode()

    assert 'data-action="previous"' in content
    assert 'data-action="next"' in content
    assert f"?thread={newest.pk}" in content
    assert f"?thread={oldest.pk}" in content


@pytest.mark.django_db
def test_triage_page_loads_the_shortcut_script(admin_client):
    content = admin_client.get(reverse("watch:triage")).content.decode()
    assert re.search(r'<script[^>]*src="[^"]*js/watch-triage\.js"[^>]*defer', content)
    assert 'x-data="triageKeyboard"' in content
    assert '@keydown.window="onKeydown($event)"' in content
    assert '@copy-citation.window="copyCitation($event.detail.button)"' in content


def test_copy_button_dispatches_the_component_event():
    text = (
        Path(settings.BASE_DIR) / "templates/watch/partials/meeting_hits.html"
    ).read_text()
    assert 'data-action="copy-citation"' in text
    assert "$dispatch('copy-citation', { button: $el })" in text


@pytest.mark.django_db
def test_staff_account_menu_links_to_triage_and_backlog(admin_client):
    content = admin_client.get(reverse("homepage")).content.decode()

    desktop_start = content.index("<!-- Account dropdown (desktop, authenticated) -->")
    desktop_end = content.index("<!-- Mobile menu button -->", desktop_start)
    mobile_start = content.index("<!-- Mobile menu panel -->")
    mobile_end = content.index("</nav>", mobile_start)
    desktop_menu = content[desktop_start:desktop_end]
    mobile_menu = content[mobile_start:mobile_end]

    for name in ("watch:triage", "watch:backlog"):
        url = reverse(name)
        assert f'href="{url}"' in desktop_menu
        assert f'href="{url}"' in mobile_menu


@pytest.mark.django_db
def test_non_staff_account_menu_hides_triage_and_backlog(authenticated_client):
    content = authenticated_client.get(reverse("homepage")).content.decode()
    assert reverse("watch:triage") not in content
    assert reverse("watch:backlog") not in content
