"""Static checks for the triage keyboard script and staff account navigation."""

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

SCRIPT = Path(settings.BASE_DIR) / "frontend/js/watch-triage.js"


def test_triage_script_has_shortcuts_and_copy():
    text = SCRIPT.read_text()
    for key in ["j", "k", "r", "d", "s", "b", "p", "m", "x", "e", "c"]:
        assert f'"{key}"' in text
    assert "navigator.clipboard.writeText" in text


def test_triage_script_ignores_form_controls():
    text = SCRIPT.read_text()
    for tag in ["INPUT", "TEXTAREA", "SELECT"]:
        assert tag in text
    assert "contenteditable" in text


def test_triage_script_has_clipboard_fallback():
    text = SCRIPT.read_text()
    assert 'document.execCommand("copy")' in text


@pytest.mark.django_db
def test_triage_page_loads_the_shortcut_script(admin_client):
    content = admin_client.get(reverse("watch:triage")).content.decode()
    assert re.search(r'<script[^>]*src="[^"]*js/watch-triage\.js"[^>]*defer', content)


@pytest.mark.django_db
def test_staff_account_menu_links_to_triage_and_backlog(admin_client):
    content = admin_client.get(reverse("homepage")).content.decode()
    for name in ("watch:triage", "watch:backlog"):
        url = reverse(name)
        # Once in the desktop menu, once in the mobile menu.
        assert content.count(f'href="{url}"') == 2


@pytest.mark.django_db
def test_non_staff_account_menu_hides_triage_and_backlog(authenticated_client):
    content = authenticated_client.get(reverse("homepage")).content.decode()
    assert reverse("watch:triage") not in content
    assert reverse("watch:backlog") not in content
