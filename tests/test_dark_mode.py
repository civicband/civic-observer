import re
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

DARK_PAIRS = {
    "bg-white": ("dark:bg-gray-9", "dark:bg-gray-8"),
    "bg-gray-50": ("dark:bg-gray-9", "dark:bg-gray-8"),
    "bg-gray-100": ("dark:bg-gray-8",),
    "text-gray-900": ("dark:text-gray-1", "dark:text-white"),
    "text-gray-800": ("dark:text-gray-2", "dark:text-gray-1"),
    "text-gray-700": ("dark:text-gray-3", "dark:text-gray-2"),
    "text-gray-600": ("dark:text-gray-3",),
    "text-gray-500": ("dark:text-gray-4",),
    "border-gray-200": ("dark:border-gray-8",),
    "border-gray-300": ("dark:border-gray-7",),
    "divide-gray-200": ("dark:divide-gray-8",),
    "divide-gray-300": ("dark:divide-gray-7",),
    "hover:bg-gray-50": ("dark:hover:bg-gray-8", "dark:hover:bg-gray-9"),
    "hover:bg-gray-100": ("dark:hover:bg-gray-8",),
    "hover:text-gray-900": ("dark:hover:text-white", "dark:hover:text-gray-1"),
    "hover:text-gray-700": ("dark:hover:text-gray-2", "dark:hover:text-gray-3"),
    "hover:text-gray-800": ("dark:hover:text-gray-2",),
}

TEMPLATES = Path(settings.BASE_DIR) / "templates"


def _class_attributes(path):
    text = path.read_text()
    text = re.sub(r"<script\b.*?</script>", " ", text, flags=re.DOTALL | re.I)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.DOTALL | re.I)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    for match in re.finditer(r'class="([^"]*)"', text, flags=re.DOTALL):
        attrs = re.sub(r"{%.*?%}", " ", match.group(1), flags=re.DOTALL)
        attrs = re.sub(r"{{.*?}}", " ", attrs, flags=re.DOTALL)
        yield attrs


def missing_dark_variants(paths):
    missing = []
    for path in paths:
        for attrs in _class_attributes(path):
            tokens = set(attrs.split())
            for light, prefixes in DARK_PAIRS.items():
                if light not in tokens:
                    continue
                if not any(
                    token.startswith(prefix) for token in tokens for prefix in prefixes
                ):
                    missing.append(f"{path.relative_to(settings.BASE_DIR)}: {light}")
    return missing


def assert_dark_variants(paths):
    missing = missing_dark_variants(paths)
    assert not missing, "Missing dark variants:\n" + "\n".join(sorted(missing))


def test_tailwind_source_configures_class_dark_variant():
    source = (Path(settings.BASE_DIR) / "frontend/css/source.css").read_text()
    assert '@import "tailwindcss";' in source
    assert "@custom-variant dark (&:where(.dark, .dark *));" in source


@pytest.mark.django_db
def test_base_template_ships_theme_bootstrap_and_toggle(client):
    response = client.get(reverse("homepage"))
    content = response.content.decode()

    assert 'var STORAGE_KEY = "civic-theme";' in content
    assert "localStorage.getItem(STORAGE_KEY)" in content
    assert "localStorage.setItem(STORAGE_KEY, mode)" in content
    assert "localStorage.removeItem(STORAGE_KEY)" in content
    assert "window.civicTheme" in content
    assert "data-theme-mode" in content
    assert "data-theme-toggle" in content
    assert "root.style.colorScheme" in content
    assert 'media.addEventListener("change"' in content
    assert 'addEventListener("storage"' in content
    assert 'class="theme-icon theme-icon-system"' in content
    assert 'class="theme-icon theme-icon-light"' in content
    assert 'class="theme-icon theme-icon-dark"' in content
    assert 'aria-label="Theme:' in content


def test_base_template_dark_variants():
    assert_dark_variants([TEMPLATES / "base.html"])


def test_public_pages_have_dark_variants():
    assert_dark_variants(
        [
            TEMPLATES / "homepage.html",
            TEMPLATES / "login.html",
            TEMPLATES / "api.html",
            TEMPLATES / "stagedoor_approval_needed.html",
            TEMPLATES / "stagedoor_token_input.html",
        ]
    )


@pytest.mark.django_db
def test_homepage_renders_dark_shell(client):
    response = client.get(reverse("homepage"))
    content = response.content.decode()

    assert "dark:bg-gray-950" in content
    assert "dark:text-gray-100" in content
    assert "dark:bg-gray-900" in content
    assert "dark:border-gray-800" in content


def test_meeting_pages_have_dark_variants():
    assert_dark_variants(
        [
            TEMPLATES / "meetings/meeting_search.html",
            TEMPLATES / "meetings/partials/search_results.html",
        ]
    )
