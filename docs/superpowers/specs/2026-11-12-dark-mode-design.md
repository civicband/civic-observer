# Dark Mode Across CivicObserver

Date: 2026-11-12
Status: Draft for review

## Summary

Add a full dark theme to every user-facing CivicObserver page, with a three-state
System / Light / Dark control in the desktop and mobile navigation. The chosen theme
persists in `localStorage`; with no explicit choice, the app follows the operating
system preference.

## Goals

- Dark theme across all user-facing templates rendered from `templates/base.html`.
- A navbar theme control available on desktop and mobile.
- System / Light / Dark modes, with System as the default.
- No flash of the wrong theme on initial paint.
- Preserve existing layout, branding, imagery, and behavior.
- No database migration or per-user preference.

## Non-goals

- Django admin theming.
- Transactional email dark mode.
- The django-rq dashboard or other third-party surfaces.
- Per-user theme sync across devices.
- A visual redesign beyond color and contrast changes.

## Current State

- Tailwind CSS v4 is built through `django-tailwind-cli`.
- The Tailwind source currently lives in ignored `.django_tailwind_cli/source.css`.
- Built CSS is written to ignored `frontend/css/tailwind.css`.
- No class-based `dark` variant is configured.
- User-facing templates use explicit neutral utilities such as `bg-white`,
  `bg-gray-50`, `text-gray-900`, and `border-gray-200`. There are roughly 1,019
  neutral utility occurrences across 62 templates, plus colored badges, alerts, and
  buttons.

## Architecture

### Tailwind source and variant

- Add tracked `frontend/css/source.css`:
  ```css
  @import "tailwindcss";
  @custom-variant dark (&:where(.dark, .dark *));
  ```
- Set `TAILWIND_CLI_SRC_CSS = "frontend/css/source.css"` in
  `config/settings/base.py`.
- Keep the existing output path (`frontend/css/tailwind.css`) and build command.
- This makes the dark variant reproducible instead of depending on an ignored
  generated source file.

### Theme bootstrap

Add a small inline script at the start of `<head>` in `templates/base.html`, before
the stylesheet:

- Read `localStorage["civic-theme"]`.
- Resolve the active mode:
  - `light` or `dark` when stored explicitly.
  - `system` when absent, invalid, or storage is unavailable.
- Apply `.dark` to `<html>` when the resolved theme is dark.
- Set `document.documentElement.dataset.themeMode` to `system`, `light`, or `dark`.
- Set `document.documentElement.style.colorScheme` to match the resolved theme, so
  native form controls and scrollbars adapt.
- While in system mode, listen to `matchMedia("(prefers-color-scheme: dark)")` and
  update the resolved theme when the OS preference changes.
- Listen for the `storage` event so other tabs follow a theme change.
- Expose `window.civicTheme` with `mode`, `resolved`, `set(mode)`, and `cycle()`.

The script must run before the page paints to avoid a light flash in dark mode.
All storage access is wrapped so private-mode or blocked-storage failures fall back
to System.

### Navbar control

- Add one theme button to the desktop navigation and one to the mobile menu.
- The button cycles System → Light → Dark → System.
- Render three decorative icons in the button (system, sun, moon) and show only the
  active one using `data-theme-mode` selectors in `<head>` CSS. This avoids icon
  flicker before Alpine or other deferred scripts load.
- Use `aria-label` and `title` that name the current mode and the next action, e.g.
  “Theme: System (follow device) — switch to Light”.
- Keep the control keyboard-accessible with the existing focus-visible styles.
- No visible text label in the navbar; the icons and accessible label carry meaning.

### Persistence

- Storage key: `civic-theme`.
- Explicit `light` and `dark` choices are stored.
- System mode removes the key.
- Invalid stored values are treated as System.
- Storage errors are ignored.

## Visual Design

Use dark neutrals for surfaces and text, keeping indigo as the primary accent.

| Purpose | Light | Dark addition |
|---|---|---|
| Page background | `bg-white` | `dark:bg-gray-950` |
| Default text | `text-gray-900` | `dark:text-gray-100` |
| Card / nav surface | `bg-white` | `dark:bg-gray-900` |
| Muted panel | `bg-gray-50` | `dark:bg-gray-900` |
| Subtle border | `border-gray-200` | `dark:border-gray-800` |
| Stronger border | `border-gray-300` | `dark:border-gray-700` |
| Secondary text | `text-gray-600` | `dark:text-gray-300` |
| Muted text | `text-gray-500` | `dark:text-gray-400` |
| Faint text | `text-gray-400` | `dark:text-gray-500` |
| Neutral hover | `hover:bg-gray-50` | `dark:hover:bg-gray-800` |
| Neutral hover text | `hover:text-gray-900` | `dark:hover:text-white` |
| Input surface | `bg-white` | `dark:bg-gray-900` |
| Input text | `text-gray-900` | `dark:text-gray-100` |
| Input border | `border-gray-300` | `dark:border-gray-700` |

Additional rules:

- Primary buttons with `bg-indigo-600 text-white` remain; use a lighter indigo hover
  in dark mode where useful.
- Secondary buttons switch to dark surfaces and lighter text.
- Status badges map light tinted backgrounds to dark tinted backgrounds with light
  text, e.g. `bg-emerald-100 text-emerald-800` becomes
  `dark:bg-emerald-950 dark:text-emerald-200`. Apply the same pattern to red,
  amber, blue, yellow, and indigo badges and alerts.
- Footer: use `dark:bg-gray-950` and `dark:border-gray-800`; existing light text
  colors remain readable.
- Images, page previews, and color swatches are not inverted. Only their borders,
  backgrounds, and captions adapt.
- Links keep indigo accents; use lighter indigo (`indigo-300`/`indigo-400`) for dark
  hover and link states where needed.

## Coverage

All user-facing templates and their partials:

- `templates/base.html`, `homepage.html`, `login.html`, `api.html`
- `templates/stagedoor_*`
- `templates/meetings/`
- `templates/municipalities/`
- `templates/searches/`
- `templates/research/`
- `templates/notifications/`
- `templates/apikeys/`
- `templates/clip/`

Excluded: `templates/email/`, Django admin templates, and third-party dashboards.

Existing and new templates must use the same dark patterns. Any future user-facing
template is expected to include dark variants for neutral surfaces, text, borders,
and form controls.

## Component Patterns

- Cards: `bg-white dark:bg-gray-900`, `border-gray-200 dark:border-gray-800`,
  headings `text-gray-900 dark:text-gray-100`.
- Forms: inputs, selects, and textareas get dark surface, text, and border classes;
  placeholders and disabled states remain legible.
- Tables: header surfaces, row dividers, and row hover states get dark variants.
- Alerts: preserve semantic color, but use dark tinted backgrounds and light text.
- Empty states: muted panel and text combinations.
- Navigation: dark surface, subtle border, readable links, and visible active/hover
  states.
- Code/color swatches: keep the user-selected color; adapt the border.

## Accessibility

- Meet WCAG AA contrast for body text and interactive controls in both themes.
- Keep focus-visible outlines visible on dark surfaces.
- The theme button must have an accessible name and reflect the current mode.
- Icons are decorative (`aria-hidden="true"`).
- Do not invert or filter page images and user content.
- JS-disabled fallback is the current light theme; no control is broken or hidden
  behind JS.

## Testing

Automated:

- Django tests assert the theme bootstrap and toggle markup are present in
  `base.html`.
- Targeted page-render tests assert representative dark classes appear on key pages.
- A template coverage test scans user-facing templates for core neutral surface,
  text, and border utilities and fails when a matching dark variant is missing.
- Full test suite, `ruff`, `mypy`, and pre-commit pass.
- Tailwind build succeeds and includes the dark variant.

Manual QA matrix:

- Desktop and mobile navigation.
- System, Light, and Dark modes.
- Initial load in both OS preferences, including no flash.
- Toggling while signed in and signed out.
- localStorage disabled/blocked.
- OS preference change while in System mode.
- Keyboard-only navigation and focus visibility.
- Page-by-page review of each app area listed in Coverage.

## Rollout

Implement in batches so regressions are easy to isolate:

1. Tailwind source/config, theme bootstrap, navbar control, and base template.
2. Homepage, auth/stagedoor, API page.
3. Meetings/search.
4. Municipalities.
5. Saved searches.
6. Research.
7. Notifications, API keys, and clip.
8. Coverage test, full QA, and final cleanup.

Each batch runs its focused tests before moving on. The full suite runs at the end.

## Risks

- Missing a template surface, producing light patches in dark mode. The coverage test
  and page-by-page QA mitigate this.
- Over-darkening semantic badges or alerts. Use dark tinted backgrounds rather than
  neutral surfaces for semantic colors.
- FOUC from script ordering. The bootstrap must stay inline and before the
  stylesheet.
- Native control colors diverging from the theme. `color-scheme` is set by the
  bootstrap.
- Large diff. Batching by app keeps review manageable.
