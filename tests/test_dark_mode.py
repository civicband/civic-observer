import re
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

from apikeys.forms import APIKeyCreateForm
from meetings.forms import MeetingSearchForm
from notifications.forms import DigestSubscriptionForm, NotificationChannelForm

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
    "border-gray-300": ("dark:border-gray-7", "dark:border-gray-6"),
    "divide-gray-200": ("dark:divide-gray-8",),
    "divide-gray-300": ("dark:divide-gray-7",),
    "hover:bg-gray-50": ("dark:hover:bg-gray-8", "dark:hover:bg-gray-9"),
    "hover:bg-gray-100": ("dark:hover:bg-gray-8",),
    "hover:text-gray-900": ("dark:hover:text-white", "dark:hover:text-gray-1"),
    "hover:text-gray-700": ("dark:hover:text-gray-2", "dark:hover:text-gray-3"),
    "hover:text-gray-800": ("dark:hover:text-gray-2",),
    "bg-gray-200": ("dark:bg-gray-7", "dark:bg-gray-8"),
    "ring-gray-300": ("dark:ring-gray-7",),
    "bg-blue-100": ("dark:bg-blue-9",),
    "text-blue-800": ("dark:text-blue-2",),
    "text-blue-700": ("dark:text-blue-3",),
    "text-blue-600": ("dark:text-blue-4",),
    "bg-green-100": ("dark:bg-green-9",),
    "text-green-800": ("dark:text-green-2",),
    "text-green-700": ("dark:text-green-3",),
    "text-green-600": ("dark:text-green-4",),
    "bg-yellow-100": ("dark:bg-yellow-9",),
    "text-yellow-800": ("dark:text-yellow-2",),
    "text-yellow-700": ("dark:text-yellow-3",),
    "text-yellow-600": ("dark:text-yellow-4",),
    "hover:text-yellow-900": ("dark:hover:text-yellow-2",),
    "bg-sky-100": ("dark:bg-sky-9",),
    "text-sky-600": ("dark:text-sky-4",),
    "bg-indigo-100": ("dark:bg-indigo-9",),
    "text-indigo-800": ("dark:text-indigo-2",),
    "text-indigo-700": ("dark:text-indigo-3",),
    "text-indigo-600": ("dark:text-indigo-4", "dark:text-indigo-3"),
    "bg-indigo-50": ("dark:bg-indigo-9",),
    "border-indigo-200": ("dark:border-indigo-8",),
    "border-indigo-100": ("dark:border-indigo-8",),
    "hover:text-indigo-600": ("dark:hover:text-indigo-4",),
    "hover:bg-indigo-100": ("dark:hover:bg-indigo-9",),
    "bg-amber-100": ("dark:bg-amber-9",),
    "text-amber-800": ("dark:text-amber-2",),
    "bg-amber-50": ("dark:bg-amber-9",),
    "border-amber-200": ("dark:border-amber-8",),
    "text-amber-700": ("dark:text-amber-3",),
    "text-amber-900": ("dark:text-amber-1",),
    "hover:bg-amber-100": ("dark:hover:bg-amber-9",),
    "bg-red-100": ("dark:bg-red-9",),
    "text-red-800": ("dark:text-red-2",),
    "bg-red-50": ("dark:bg-red-9",),
    "border-red-200": ("dark:border-red-8",),
    "border-red-300": ("dark:border-red-7",),
    "text-red-700": ("dark:text-red-3",),
    "text-red-600": ("dark:text-red-4",),
    "text-red-900": ("dark:text-red-1",),
    "placeholder-red-300": ("dark:placeholder-red-4",),
    "bg-emerald-100": ("dark:bg-emerald-9",),
    "text-emerald-800": ("dark:text-emerald-2",),
    "bg-emerald-50": ("dark:bg-emerald-9",),
    "border-emerald-200": ("dark:border-emerald-8",),
    "text-emerald-700": ("dark:text-emerald-3",),
    "text-emerald-600": ("dark:text-emerald-4",),
    "bg-rose-100": ("dark:bg-rose-9",),
    "text-rose-800": ("dark:text-rose-2",),
    "bg-rose-50": ("dark:bg-rose-9",),
    "border-rose-200": ("dark:border-rose-8",),
    "text-rose-700": ("dark:text-rose-3",),
    "bg-purple-100": ("dark:bg-purple-9",),
    "text-purple-800": ("dark:text-purple-2",),
    "bg-teal-100": ("dark:bg-teal-9",),
    "text-teal-800": ("dark:text-teal-2",),
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
    assert "root.style.colorScheme" in content
    assert "window.__civicThemeBoot" in content
    assert "data-theme-mode" in content
    assert "data-theme-toggle" in content
    assert 'class="theme-icon theme-icon-system"' in content
    assert 'class="theme-icon theme-icon-light"' in content
    assert 'class="theme-icon theme-icon-dark"' in content
    assert 'aria-label="Theme:' in content
    assert "js/theme.js" in content
    assert content.index("js/theme.js") < content.index("alpinejs")


def test_theme_controller_uses_alpine_store():
    source = (Path(settings.BASE_DIR) / "frontend/js/theme.js").read_text()

    assert "alpine:init" in source
    assert 'Alpine.store("theme"' in source or "Alpine.store('theme'" in source
    assert 'media.addEventListener("change"' in source
    assert 'addEventListener("storage"' in source
    assert "localStorage.setItem" in source
    assert "localStorage.removeItem" in source
    assert "cycle: function" in source or "cycle()" in source


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


def test_municipality_pages_have_dark_variants():
    assert_dark_variants(sorted((TEMPLATES / "municipalities").rglob("*.html")))


def test_saved_search_pages_have_dark_variants():
    assert_dark_variants(sorted((TEMPLATES / "searches").rglob("*.html")))


def test_research_core_pages_have_dark_variants():
    assert_dark_variants(
        [
            TEMPLATES / "research/project_detail.html",
            TEMPLATES / "research/project_list.html",
            TEMPLATES / "research/set_detail.html",
            TEMPLATES / "research/review.html",
            *sorted((TEMPLATES / "research/partials").glob("*.html")),
        ]
    )


def test_research_secondary_pages_have_dark_variants():
    assert_dark_variants(
        [
            TEMPLATES / "research/codebook.html",
            TEMPLATES / "research/export.html",
            TEMPLATES / "research/methods.html",
            TEMPLATES / "research/timeline.html",
            *sorted((TEMPLATES / "research/rules").rglob("*.html")),
            TEMPLATES / "research/partials/codebook_tree.html",
        ]
    )


def test_account_and_clip_pages_have_dark_variants():
    assert_dark_variants(
        [
            *sorted((TEMPLATES / "notifications").rglob("*.html")),
            *sorted((TEMPLATES / "apikeys").rglob("*.html")),
            *sorted((TEMPLATES / "clip").rglob("*.html")),
        ]
    )


def test_all_user_facing_templates_have_dark_variants():
    paths = []
    for path in TEMPLATES.rglob("*.html"):
        relative = path.relative_to(TEMPLATES).as_posix()
        if relative.startswith("email/") or relative.startswith("admin/"):
            continue
        paths.append(path)
    assert_dark_variants(paths)


def _widgets(widget):
    if hasattr(widget, "widgets"):
        for sub in widget.widgets:
            yield from _widgets(sub)
    else:
        yield widget


@pytest.mark.parametrize(
    "form_class",
    [
        MeetingSearchForm,
        DigestSubscriptionForm,
        NotificationChannelForm,
        APIKeyCreateForm,
    ],
)
def test_form_widgets_have_dark_classes(form_class):
    form = form_class()
    missing = []
    for field_name, field in form.fields.items():
        for widget in _widgets(field.widget):
            input_type = getattr(widget, "input_type", "")
            if input_type == "hidden":
                continue
            classes = widget.attrs.get("class", "")
            tokens = set(classes.split())
            if input_type == "checkbox":
                if not any(token.startswith("dark:border-") for token in tokens):
                    missing.append((form_class.__name__, field_name, classes))
                continue
            if "dark:bg-gray-900" in tokens or "dark:bg-gray-800" not in tokens:
                missing.append((form_class.__name__, field_name, classes))
            if not any(token.startswith("dark:text-") for token in tokens):
                missing.append((form_class.__name__, field_name, classes))
    assert not missing, missing


def test_municipality_sort_arrows_have_dark_text():
    content = (TEMPLATES / "municipalities/muni_list.html").read_text()
    assert "'text-gray-700 dark:text-gray-300'" in content


def test_clip_page_preview_and_skeleton_have_dark_variants():
    assert_dark_variants(
        [
            TEMPLATES / "clip/partials/page_preview.html",
            TEMPLATES / "clip/partials/loading_skeleton.html",
        ]
    )


def test_digest_flash_messages_have_dark_variants():
    assert_dark_variants([TEMPLATES / "notifications/digest_list.html"])


def test_search_pagination_ring_has_dark_variant():
    assert_dark_variants([TEMPLATES / "meetings/partials/search_results.html"])


def test_homepage_dynamic_surfaces_have_dark_variants():
    assert_dark_variants([TEMPLATES / "homepage.html"])


def test_theme_toggle_is_hidden_without_javascript():
    content = (TEMPLATES / "base.html").read_text()
    assert (
        "<noscript><style>[data-theme-toggle]{display:none}</style></noscript>"
        in content
    )


def test_visible_form_controls_have_dark_surface():
    control_pattern = re.compile(
        r"<(input|select|textarea)\b([^>]*)>", re.DOTALL | re.I
    )
    missing = []
    for path in TEMPLATES.rglob("*.html"):
        relative = path.relative_to(TEMPLATES).as_posix()
        if relative.startswith("email/") or relative.startswith("admin/"):
            continue
        text = path.read_text()
        for tag, attrs in control_pattern.findall(text):
            tag_name = tag.lower()
            if re.search(r"\bhidden\b", attrs, re.I):
                continue
            if tag_name == "input" and re.search(
                r'type="(hidden|checkbox|radio|submit|button)"', attrs, re.I
            ):
                continue
            class_match = re.search(r'class="([^"]*)"', attrs, re.DOTALL)
            if not class_match:
                continue
            classes = class_match.group(1)
            tokens = set(classes.split())
            if "dark:bg-gray-900" in tokens or "dark:bg-gray-800" not in tokens:
                missing.append(f"{relative}: <{tag_name}> {classes}")
            if not any(token.startswith("dark:text-") for token in tokens):
                missing.append(f"{relative}: <{tag_name}> {classes}")
    assert not missing, "Missing dark form surface:\n" + "\n".join(sorted(missing))
