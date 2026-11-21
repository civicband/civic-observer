"""Optional LLM place classification.

The deterministic extractor is the primary path; this module is a best-effort
fallback behind ``WATCH_LLM_*`` settings and is disabled by default. It speaks
the OpenAI-compatible ``/chat/completions`` shape through plain ``httpx`` so any
compatible endpoint can be used. Failures never reach the caller: a missing
setting, a malformed response, or any network error returns ``None``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from django.conf import settings

from watch.models import Thread

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You extract places from civic discussion text. Reply with one JSON object "
    'whose keys are "places", "is_us", and "topic". "places" is an array of '
    'objects with exactly "name" (string), "state" (two-letter US state code, '
    'or an empty string when unknown), and "role" ("subject" for the place the '
    'discussion is about, or "mentioned" for every other place). "is_us" is a '
    'boolean. "topic" is a short search phrase for civic meeting documents. '
    "Use only place names that appear in the text, and reply with JSON only."
)

_ROLES = frozenset({"subject", "mentioned"})

#: The model may list many places; keep a small cap so one bad response cannot
#: flood a mention with rows.
MAX_LLM_PLACES = 20

#: The prompt is capped so a long comment chain cannot exceed a small model's
#: context window. Grounding checks must use the same truncated text.
MAX_THREAD_TEXT = 8000


@dataclass(frozen=True)
class LLMPlace:
    """One place named by the model."""

    name: str
    state: str
    role: str


@dataclass(frozen=True)
class LLMResult:
    """The model's classification of one thread."""

    places: list[LLMPlace]
    is_us: bool
    topic: str


def classify_with_llm(thread: Thread) -> LLMResult | None:
    """Return the model's classification of ``thread``, or ``None``.

    Returns immediately when the pass is disabled or unconfigured, and catches
    every exception so a flaky endpoint can never break ingestion. The warning
    intentionally omits thread text so alert snippets stay out of the logs.
    """
    if not settings.WATCH_LLM_ENABLED:
        return None

    base_url = settings.WATCH_LLM_BASE_URL.strip().rstrip("/")
    model = settings.WATCH_LLM_MODEL.strip()
    if not base_url or not model:
        return None

    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers=_headers(),
            json=_request_body(thread, model),
            timeout=settings.WATCH_LLM_TIMEOUT,
        )
        response.raise_for_status()
        return _parse_response(response.json())
    except Exception as exc:
        logger.warning("LLM place classification failed (%s)", type(exc).__name__)
        return None


def _headers() -> dict[str, str]:
    """Return request headers, adding a bearer token when one is configured."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    api_key = settings.WATCH_LLM_API_KEY.strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _request_body(thread: Thread, model: str) -> dict[str, Any]:
    """Build the OpenAI-compatible request body for one thread."""
    return {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _thread_text(thread)},
        ],
    }


def _thread_text(thread: Thread) -> str:
    """Return the thread title and mention text as one prompt string.

    The prompt is capped so a long comment chain cannot exceed a small model's
    context window. Empty parts are skipped to keep the prompt tidy.
    """
    parts: list[str] = [thread.title]
    for mention in thread.mentions.order_by("pk"):
        parts.append(mention.post_title)
        parts.append(mention.snippet_text)
    text = "\n\n".join(part.strip() for part in parts if part and part.strip())
    return text[:MAX_THREAD_TEXT]


def _parse_response(payload: Any) -> LLMResult | None:
    """Validate an HTTP response payload and build a result, or return ``None``.

    Every key is checked before a dataclass is constructed. A malformed envelope,
    content, or place item invalidates the whole response rather than silently
    keeping a partial guess.
    """
    content = _message_content(payload)
    if content is None:
        return None
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    raw_places = data.get("places")
    is_us = data.get("is_us")
    topic = data.get("topic")
    if not isinstance(raw_places, list):
        return None
    if not isinstance(is_us, bool):
        return None
    if not isinstance(topic, str):
        return None

    places: list[LLMPlace] = []
    for item in raw_places[:MAX_LLM_PLACES]:
        if not isinstance(item, dict):
            return None
        name = item.get("name")
        state = item.get("state")
        role = item.get("role")
        if not isinstance(name, str) or not name.strip():
            return None
        if not isinstance(state, str):
            return None
        if not isinstance(role, str) or role not in _ROLES:
            return None
        places.append(LLMPlace(name=name.strip(), state=_clean_state(state), role=role))

    return LLMResult(places=places, is_us=is_us, topic=topic)


def _clean_state(state: str) -> str:
    """Return a normalized two-letter state code, or an empty string."""
    code = state.strip().upper()
    if len(code) == 2 and code.isalpha():
        return code
    return ""


def _message_content(payload: Any) -> str | None:
    """Return the assistant message content from a chat-completions payload."""
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    choice = choices[0]
    if not isinstance(choice, dict):
        return None
    message = choice.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None
