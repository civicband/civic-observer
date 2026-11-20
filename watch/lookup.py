"""Build local meeting-search queries for CivicBand Watch threads.

The query is deliberately simple: a thread title plus proper-noun phrases found
in its alert snippets, with noise removed.  It does not call an LLM or an
external NLP service, so the same thread always produces the same query.
"""

from __future__ import annotations

import re

from watch.models import Mention, Place, Thread, ThreadPlace

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
        "title",
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

_TERM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’-]*")
_CAPITALIZED_RE = re.compile(r"[A-Z][A-Za-z'’-]*(?:\s+[A-Z][A-Za-z'’-]*)*")
_DETERMINERS = frozenset({"a", "an", "the"})


def capitalized_phrases(text: str) -> list[str]:
    """Return candidate proper-noun phrases from ``text``, in order.

    Each run of capitalized words contributes the full run and its most
    useful shorter forms: the run without a leading determiner (or its leading
    two words), and its trailing two-word phrase.  Meeting documents often
    refer to "The Beverly Hills Planning Commission" as "Beverly Hills
    Planning Commission" or simply "Planning Commission".
    """
    phrases: list[str] = []
    for match in _CAPITALIZED_RE.findall(text):
        words = match.split()
        phrases.append(match)
        if len(words) < 2:
            continue
        if words[0].lower() in _DETERMINERS:
            phrases.append(" ".join(words[1:]))
        elif len(words) > 2:
            phrases.append(" ".join(words[:2]))
        if len(words) > 2:
            phrases.append(" ".join(words[-2:]))
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

    mentions = list(thread.mentions.all())
    banned = _banned_phrases(mentions, place)

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


def _banned_phrases(mentions: list[Mention], place: Place | None) -> list[str]:
    """Return alert keywords, the place name, and its alias keys."""
    phrases = [
        mention.keyword.strip() for mention in mentions if mention.keyword.strip()
    ]
    if place is not None:
        phrases.append(place.name)
        phrases.extend(place.aliases.values_list("key", flat=True))
    return phrases


def _query_terms(text: str, banned: list[str]) -> list[str]:
    """Return lower-case query terms from ``text`` with banned text removed."""
    if banned:
        pattern = re.compile(
            r"\b(?:"
            + "|".join(
                re.escape(phrase) for phrase in sorted(banned, key=len, reverse=True)
            )
            + r")\b",
            re.IGNORECASE,
        )
        text = pattern.sub(" ", text)
    return [
        token.lower()
        for token in _TERM_RE.findall(text)
        if len(token) >= 3 and token.lower() not in STOPWORDS
    ]
