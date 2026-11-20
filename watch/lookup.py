"""Build local meeting-search queries for CivicBand Watch threads.

The query is deliberately simple: a thread title plus proper-noun phrases found
in its alert snippets, with noise removed.  It does not call an LLM or an
external NLP service, so the same thread always produces the same query.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import TYPE_CHECKING

from django.utils import timezone

from searches import search_backends
from watch.models import Mention, Place, Thread, ThreadPlace

if TYPE_CHECKING:
    from municipalities.models import Muni

#: Maximum number of unique terms returned by :func:`build_query`.
MAX_QUERY_TERMS = 25

#: Lower-case words that add noise to a meeting-document search.  The list
#: combines common English function words with the vocabulary of Reddit alerts
#: and municipal government.
STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "also",
        "am",
        "an",
        "and",
        "another",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "just",
        "me",
        "more",
        "most",
        "much",
        "must",
        "my",
        "myself",
        "no",
        "nor",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
        # Reddit and alert vocabulary.
        "alert",
        "article",
        "comment",
        "comments",
        "discussion",
        "link",
        "post",
        "reddit",
        "subreddit",
        "thread",
        "update",
        "updates",
        # Municipal and news vocabulary.
        "board",
        "city",
        "commission",
        "council",
        "county",
        "meeting",
        "meetings",
        "news",
        "today",
        "tomorrow",
        "week",
        "year",
        "yesterday",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_MAX_PHRASE_WORDS = 4
_CAPITALIZED_RUN_RE = re.compile(r"[A-Z][A-Za-z'’-]*(?:\s+[A-Z][A-Za-z'’-]*)*")
_DETERMINERS = frozenset({"a", "an", "the"})


def capitalized_phrases(text: str) -> list[str]:
    """Return contiguous capitalized phrases of one to four words, in order.

    Each capitalized run is expanded into every contiguous sub-phrase of one
    to four words (sliding windows) after dropping a leading determiner such
    as ``The``.  Windows start at each word and grow one word at a time, so
    the tail of a long run is preserved.  Duplicate phrases are removed
    case-insensitively while keeping their first position.
    """
    phrases: list[str] = []
    seen: set[str] = set()
    for match in _CAPITALIZED_RUN_RE.findall(text):
        words = match.split()
        if words and words[0].lower() in _DETERMINERS:
            words = words[1:]
        for start in range(len(words)):
            longest = min(_MAX_PHRASE_WORDS, len(words) - start)
            for length in range(1, longest + 1):
                phrase = " ".join(words[start : start + length])
                key = phrase.lower()
                if key in seen:
                    continue
                seen.add(key)
                phrases.append(phrase)
    return phrases


def build_query(thread: Thread, *, place: Place | None = None) -> str:
    """Build a local search query for ``thread``, optionally scoped by place.

    A non-blank :attr:`~watch.models.ThreadPlace.query_override` for ``place``
    wins outright and is returned unchanged.  Otherwise the query is composed
    of title terms and capitalized snippet phrases, minus alert keywords, the
    place name and its aliases, stopwords, and very short tokens.
    """
    if place is not None:
        override = (
            ThreadPlace.objects.filter(thread=thread, place=place)
            .values_list("query_override", flat=True)
            .first()
        )
        if override and override.strip():
            return override

    mentions = list(thread.mentions.order_by("published_at", "pk"))
    banned = _banned_tokens(mentions, place)

    texts: list[str] = []
    if thread.title:
        texts.append(thread.title)
    for mention in mentions:
        if mention.snippet_text:
            texts.extend(capitalized_phrases(mention.snippet_text))

    terms: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for term in _query_terms(text, banned):
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            terms.append(key)
            if len(terms) >= MAX_QUERY_TERMS:
                break
        if len(terms) >= MAX_QUERY_TERMS:
            break

    if terms:
        return " ".join(terms)

    fallback = thread.title.strip()
    if not fallback and place is not None:
        fallback = place.name.strip()
    return fallback


def search_place(place: Place, query: str, *, limit: int = 5) -> list[dict]:
    """Return meeting-page results for ``query`` near ``place``.

    Places without a linked :class:`~municipalities.models.Muni` cannot be
    searched and return an empty list.  Results from the last 365 days are
    tried first, then the search is retried without a date bound only when
    that window produced nothing, so older meetings can still match.
    """
    if place.muni is None:
        return []

    date_from = timezone.localdate() - timedelta(days=365)
    results, _total = _search(query, place.muni, date_from=date_from, limit=limit)
    if results:
        return results

    results, _total = _search(query, place.muni, date_from=None, limit=limit)
    return results


def _search(
    query: str, muni: Muni, *, date_from: date | None, limit: int
) -> tuple[list[dict], int]:
    """Run one backend search scoped to ``muni`` and return results and total."""
    backend = search_backends.get_search_backend()
    return backend.search_with_cache(
        query_text=query,
        municipalities=[muni],
        date_from=date_from,
        limit=limit,
    )


def _banned_tokens(mentions: list[Mention], place: Place | None) -> set[str]:
    """Return normalized tokens from alert keywords, the place, and its aliases.

    Matching on tokens rather than on a regex keeps aliases containing
    punctuation (for example ``St. Yuba``) working as intended.
    """
    texts = [mention.keyword for mention in mentions if mention.keyword.strip()]
    if place is not None:
        texts.append(place.name)
        texts.extend(place.aliases.values_list("key", flat=True))

    banned: set[str] = set()
    for text in texts:
        banned.update(_normalized_tokens(text))
    return banned


def _normalized_tokens(text: str) -> list[str]:
    """Return lower-case alphanumeric tokens for ``text``.

    Splitting on punctuation keeps possessive forms such as ``Chico's`` tied
    to the banned token ``chico`` and splits hyphenated aliases consistently.
    """
    return _TOKEN_RE.findall(text.lower())


def _query_terms(text: str, banned: set[str]) -> list[str]:
    """Return lower-case query terms from ``text``, minus noise and bans."""
    terms: list[str] = []
    for key in _normalized_tokens(text):
        if len(key) >= 3 and key not in STOPWORDS and key not in banned:
            terms.append(key)
    return terms
