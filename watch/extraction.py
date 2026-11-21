"""Deterministic place extraction from thread text.

This module owns the body-pattern rules and the alias-based phrase resolver.
Every place must already exist as a :class:`~watch.models.Place` row; nothing
here creates a place from free text. Body-pattern matches are high-confidence
subjects because phrases such as "Chico City Council" name a governing body.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction

from watch.llm import classify_with_llm
from watch.models import (
    Mention,
    Place,
    PlaceAlias,
    PlaceMention,
    Subreddit,
    Thread,
    ThreadPlace,
)
from watch.normalize import normalize_key
from watch.place_utils import SETTLEMENT_TYPES, prefer_settlement_types

#: Place names that are also ordinary English words. A lower-case use of one
#: is prose, not a reference to the place, so resolution requires a
#: capitalized phrase.
COMMON_WORD_NAMES: frozenset[str] = frozenset(
    {
        "athens",
        "bell",
        "clinton",
        "concord",
        "hope",
        "independence",
        "jackson",
        "lebanon",
        "liberty",
        "madison",
        "marion",
        "mobile",
        "riverside",
        "salem",
        "union",
        "washington",
    }
)

#: Local-government types that can have a city or town council.
MUNICIPAL_TYPES: frozenset[str] = frozenset(
    {
        Place.PlaceType.CITY,
        Place.PlaceType.TOWN,
        Place.PlaceType.VILLAGE,
    }
)

#: Types that can operate a planning commission or similar body. CDPs are
#: included because their extracted place rolls up to the containing county.
GOVERNMENT_TYPES: frozenset[str] = (
    MUNICIPAL_TYPES | SETTLEMENT_TYPES | {Place.PlaceType.COUNTY}
)

#: A bounded place phrase of one to five capitalized words. Patterns compile
#: with ``re.IGNORECASE`` so the suffix ("city council") matches any case; the
#: scoped ``(?-i:...)`` keeps the captured phrase genuinely capitalized so the
#: pattern cannot swallow the lower-case words that follow the place.
PLACE = r"(?P<place>(?-i:[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,4}))"

_BODY_PATTERN_METHOD = PlaceMention.Method.BODY_PATTERN

#: Methods this module owns and regenerates on every extraction pass.
_DETERMINISTIC_METHODS = (
    PlaceMention.Method.BODY_PATTERN,
    PlaceMention.Method.GAZETTEER_PHRASE,
)

#: Methods owned elsewhere. Deterministic extraction preserves these rows; the
#: optional LLM pass clears and regenerates its own rows.
_PRESERVED_METHODS = (
    PlaceMention.Method.MANUAL,
    PlaceMention.Method.LLM,
)


#: ``(pattern, required_place_type, method)`` rules, ordered from most to
#: least specific so "Riverside County Board of Supervisors" is not also read
#: as a bare board phrase.
BODY_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(rf"{PLACE}\s+City\s+Council\b", re.IGNORECASE),
        "municipality",
        _BODY_PATTERN_METHOD,
    ),
    (
        re.compile(
            rf"{PLACE}\s+County\s+Board\s+of\s+Supervisors\b",
            re.IGNORECASE,
        ),
        Place.PlaceType.COUNTY,
        _BODY_PATTERN_METHOD,
    ),
    (
        re.compile(rf"{PLACE}\s+Board\s+of\s+Supervisors\b", re.IGNORECASE),
        Place.PlaceType.COUNTY,
        _BODY_PATTERN_METHOD,
    ),
    (
        re.compile(rf"{PLACE}\s+Planning\s+Commission\b", re.IGNORECASE),
        "government",
        _BODY_PATTERN_METHOD,
    ),
    (
        re.compile(rf"\bTown\s+of\s+{PLACE}\b", re.IGNORECASE),
        Place.PlaceType.TOWN,
        _BODY_PATTERN_METHOD,
    ),
    (
        re.compile(rf"\bCity\s+of\s+{PLACE}\b", re.IGNORECASE),
        Place.PlaceType.CITY,
        _BODY_PATTERN_METHOD,
    ),
    (
        re.compile(rf"\bCounty\s+of\s+{PLACE}\b", re.IGNORECASE),
        Place.PlaceType.COUNTY,
        _BODY_PATTERN_METHOD,
    ),
]

#: The runner-up must be at least this many times smaller before the most
#: populous candidate wins an otherwise ambiguous phrase.
AMBIGUITY_RATIO = 10

#: A capitalized word, allowing internal apostrophes, hyphens, and initials.
#: The negative lookbehind keeps a capitalized fragment inside a mixed-case
#: brand ("iPhone", "eBay") from being read as a place. Unicode letters are
#: included so names such as ``Española`` and ``Doña Ana`` tokenize whole.
_CAPITALIZED_TOKEN = re.compile(r"(?<!\w)[^\W\d_][\w'’.\-]*")

#: Phrases longer than this are almost always a run of title-case prose.
_MAX_PHRASE_WORDS = 4

_PHRASE_METHOD = PlaceMention.Method.GAZETTEER_PHRASE

#: Sentence-ending punctuation used to decide whether a common-word name is
#: prose. A name at the start of a sentence is usually the English word.
_SENTENCE_BREAKS = frozenset(".!?\n")
_CLOSING_PUNCTUATION = "\"'”’)]"

_POSSESSIVE_RE = re.compile(r"['’]s\b", re.IGNORECASE)
_HYPHEN_RE = re.compile(r"[-–—]")

_CONFIDENCE_RANK: dict[str, int] = {
    PlaceMention.Confidence.LOW: 1,
    PlaceMention.Confidence.MEDIUM: 2,
    PlaceMention.Confidence.HIGH: 3,
}


@dataclass(frozen=True)
class ExtractedPlace:
    """One place identified in text, with the evidence that found it."""

    place: Place
    matched_text: str
    method: str
    confidence: str
    is_subject: bool


def extract_body_patterns(
    text: str, *, state: str | None = None
) -> list[ExtractedPlace]:
    """Return places named by governing-body phrases in ``text``.

    Each pattern loads its candidate aliases in one query, however many phrases
    it matches. Duplicate places are collapsed so a county board sentence does
    not produce both the specific and generic board rules.
    """
    results: list[ExtractedPlace] = []
    seen: set[tuple[object, bool]] = set()

    for pattern, required_place_type, method in BODY_PATTERNS:
        phrases = [
            phrase
            for match in pattern.finditer(text)
            if (phrase := match.group("place")) and phrase[:1].isupper()
        ]
        if not phrases:
            continue
        aliases = _load_aliases(phrases)

        for phrase in phrases:
            place, matched_text = _resolve_variants(
                phrase,
                aliases=aliases,
                state=state,
                required_place_type=required_place_type,
            )
            if place is None:
                continue

            key = (place.pk, True)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                ExtractedPlace(
                    place=place,
                    matched_text=matched_text,
                    method=method,
                    confidence=PlaceMention.Confidence.HIGH,
                    is_subject=True,
                )
            )

    return results


def extract_phrases(text: str, *, state: str | None = None) -> list[ExtractedPlace]:
    """Return places named by capitalized phrases in ``text``.

    Every capitalized run yields candidate n-grams of one to
    :data:`_MAX_PHRASE_WORDS` words. All candidate keys share one alias query,
    and each run is then walked greedily so ``Riverside County`` is one match
    rather than also ``Riverside``. These matches are unanchored: they are
    returned with low confidence and are never subjects.
    """
    runs = _capitalized_runs(text)
    if not runs:
        return []

    candidates = [
        " ".join(token for token, _ in run[start : start + size])
        for run in runs
        for start in range(len(run))
        for size in range(1, min(_MAX_PHRASE_WORDS, len(run) - start) + 1)
    ]
    aliases = _load_aliases(candidates)

    results: list[ExtractedPlace] = []
    for run in runs:
        index = 0
        while index < len(run):
            match = _match_longest_phrase(
                run,
                index,
                aliases=aliases,
                state=state,
                sentence_initial=_is_sentence_initial(text, run[index][1]),
            )
            if match is None:
                index += 1
                continue
            place, phrase, size = match
            results.append(
                ExtractedPlace(
                    place=place,
                    matched_text=phrase,
                    method=_PHRASE_METHOD,
                    confidence=PlaceMention.Confidence.LOW,
                    is_subject=False,
                )
            )
            index += size

    return _dedupe_extracted(results)


def extract_mention_places(
    mention: Mention, subreddit: Subreddit | None
) -> list[PlaceMention]:
    """Replace a mention's deterministic place rows and return the new rows.

    Body-pattern matches stay high-confidence subjects. Gazetteer phrases are
    low-confidence mentions. Manual and LLM rows survive so the optional LLM
    pass can clear and regenerate its own rows. A place already covered by a
    preserved row is not extracted again because ``(mention, place)`` is unique.
    """
    state = subreddit.state if subreddit is not None else None
    text = mention.snippet_text
    extracted = _dedupe_extracted(
        [
            *extract_body_patterns(text, state=state),
            *extract_phrases(text, state=state),
        ]
    )
    by_place = _dedupe_by_place(extracted)

    with transaction.atomic():
        preserved_place_ids = set(
            mention.place_mentions.filter(method__in=_PRESERVED_METHODS).values_list(
                "place_id", flat=True
            )
        )
        new_rows = [
            PlaceMention(
                mention=mention,
                place=item.place,
                matched_text=item.matched_text,
                method=item.method,
                confidence=item.confidence,
                is_subject=item.is_subject,
            )
            for item in by_place.values()
            if item.place.pk not in preserved_place_ids
        ]
        mention.place_mentions.filter(method__in=_DETERMINISTIC_METHODS).delete()
        return PlaceMention.objects.bulk_create(new_rows)


@dataclass
class _ThreadPlaceCandidate:
    """One place proposed for a thread, before duplicate merging."""

    place_id: object
    role: str
    confidence: str
    source: str


def extract_thread_places(thread: Thread) -> dict[str, int]:
    """Roll a thread's extracted places up into :class:`ThreadPlace` rows.

    The subreddit's own place is a subject.  Post titles and post bodies are
    subjects because the post is the thread's topic; comment places are
    subjects only when a governing-body pattern names them, and mentioned
    otherwise.  ``llm`` and ``manual`` place mentions use their stored
    ``is_subject`` flag instead of those deterministic rules.  Duplicates merge
    per place, preferring a subject and then the highest confidence.

    When ``WATCH_LLM_ENABLED`` is on and the subreddit does not pin a place, the
    deterministic methods disagree with it, or a phrase is ambiguous, the
    optional LLM pass replaces each mention's LLM rows.  Unresolved model names
    are ignored, and those rows are medium-confidence with the model's subject
    or mentioned role.

    Operator-created ``manual`` rows are never updated or deleted, including
    stale rows that extraction no longer proposes.  Returned counts include
    those manual rows.

    Deleting a stale non-manual row drops any operator ``query_override``
    stored on it. A surviving non-manual row keeps its override, and a
    ``source="manual"`` row survives even when stale.
    """
    subreddit = thread.subreddit
    state = subreddit.state if subreddit is not None else None
    candidates: dict[object, _ThreadPlaceCandidate] = {}

    if subreddit is not None and subreddit.place_id is not None:
        _merge_thread_place(
            candidates,
            _ThreadPlaceCandidate(
                place_id=subreddit.place_id,
                role=ThreadPlace.Role.SUBJECT,
                confidence=subreddit.confidence or ThreadPlace.Confidence.HIGH,
                source=ThreadPlace.Source.SUBREDDIT,
            ),
        )

    mentions = list(thread.mentions.order_by("pk"))
    deterministic_rows = {
        mention.pk: extract_mention_places(mention, subreddit) for mention in mentions
    }
    if _should_run_llm(subreddit, deterministic_rows, mentions, state):
        _regenerate_llm_places(thread, mentions, state)

    preserved_rows: dict[object, list[PlaceMention]] = {}
    for row in PlaceMention.objects.filter(
        mention__thread=thread, method__in=_PRESERVED_METHODS
    ):
        preserved_rows.setdefault(row.mention_id, []).append(row)

    for mention in mentions:
        is_post = mention.kind == Mention.Kind.POST
        for place_mention in [
            *deterministic_rows[mention.pk],
            *preserved_rows.get(mention.pk, []),
        ]:
            _merge_thread_place(
                candidates,
                _ThreadPlaceCandidate(
                    place_id=place_mention.place_id,
                    role=_thread_place_role(mention, place_mention),
                    confidence=place_mention.confidence,
                    source=(
                        ThreadPlace.Source.POST
                        if is_post
                        else ThreadPlace.Source.COMMENT
                    ),
                ),
            )
        if is_post:
            for extracted in extract_phrases(mention.post_title, state=state):
                _merge_thread_place(
                    candidates,
                    _ThreadPlaceCandidate(
                        place_id=extracted.place.pk,
                        role=ThreadPlace.Role.SUBJECT,
                        confidence=extracted.confidence,
                        source=ThreadPlace.Source.POST,
                    ),
                )

    with transaction.atomic():
        manual_place_ids = set(
            thread.thread_places.filter(source=ThreadPlace.Source.MANUAL).values_list(
                "place_id", flat=True
            )
        )
        for place_id, candidate in candidates.items():
            if place_id in manual_place_ids:
                continue
            ThreadPlace.objects.update_or_create(
                thread=thread,
                place_id=place_id,
                defaults={
                    "role": candidate.role,
                    "confidence": candidate.confidence,
                    "source": candidate.source,
                },
            )
        thread.thread_places.exclude(source=ThreadPlace.Source.MANUAL).exclude(
            place_id__in=candidates.keys()
        ).delete()
        subjects = thread.thread_places.filter(role=ThreadPlace.Role.SUBJECT).count()
        mentioned = thread.thread_places.filter(role=ThreadPlace.Role.MENTIONED).count()

    return {
        "subjects": subjects,
        "mentioned": mentioned,
        "mentions": len(mentions),
    }


#: Subreddit kinds that do not pin a thread to one place on their own, so the
#: optional LLM pass may help disambiguate the thread.
_LLM_FALLBACK_KINDS = frozenset(
    {Subreddit.Kind.GENERAL, Subreddit.Kind.REGION, Subreddit.Kind.STATE}
)


def _should_run_llm(
    subreddit: Subreddit | None,
    deterministic_rows: dict[str, list[PlaceMention]],
    mentions: list[Mention],
    state: str | None,
) -> bool:
    """Return whether this thread warrants the optional LLM pass.

    The pass is off unless enabled. It runs for subreddits that do not name a
    place, when deterministic extraction proposes a place other than the
    subreddit's, or when a capitalized phrase resolves to several places.
    """
    if not settings.WATCH_LLM_ENABLED:
        return False
    if not mentions:
        return False
    if subreddit is not None and subreddit.kind in _LLM_FALLBACK_KINDS:
        return True
    if subreddit is not None and _deterministic_disagrees(
        subreddit, deterministic_rows
    ):
        return True
    return _has_ambiguous_match(mentions, state)


def _deterministic_disagrees(
    subreddit: Subreddit, deterministic_rows: dict[str, list[PlaceMention]]
) -> bool:
    """Return whether deterministic extraction named a place besides the one
    already attached to the subreddit."""
    bound_place_id = subreddit.place_id
    for rows in deterministic_rows.values():
        for row in rows:
            if bound_place_id is None or row.place_id != bound_place_id:
                return True
    return False


def _has_ambiguous_match(mentions: list[Mention], state: str | None) -> bool:
    """Return whether any phrase could not be resolved because of ambiguity."""
    texts: list[str] = []
    for mention in mentions:
        if mention.snippet_text:
            texts.append(mention.snippet_text)
        if mention.kind == Mention.Kind.POST and mention.post_title:
            texts.append(mention.post_title)
    if not texts:
        return False

    runs_by_text = [(text, _capitalized_runs(text)) for text in texts]
    candidates = [
        " ".join(token for token, _ in run[start : start + size])
        for _, runs in runs_by_text
        for run in runs
        for start in range(len(run))
        for size in range(1, min(_MAX_PHRASE_WORDS, len(run) - start) + 1)
    ]
    aliases = _load_aliases(candidates)

    for text, runs in runs_by_text:
        for run in runs:
            index = 0
            while index < len(run):
                ambiguous, size = _scan_phrase_for_ambiguity(
                    run,
                    index,
                    aliases=aliases,
                    state=state,
                    sentence_initial=_is_sentence_initial(text, run[index][1]),
                )
                if ambiguous:
                    return True
                index += size or 1
    return False


def _scan_phrase_for_ambiguity(
    run: list[tuple[str, int]],
    index: int,
    *,
    aliases: dict[str, list[Place]],
    state: str | None,
    sentence_initial: bool,
) -> tuple[bool, int]:
    """Return ``(ambiguous, matched_size)`` for the longest phrase at ``index``.

    A longer resolvable phrase wins, mirroring :func:`_match_longest_phrase`.
    Only when no length resolves does an unresolved candidate pool count as
    ambiguous.
    """
    longest = min(_MAX_PHRASE_WORDS, len(run) - index)
    for size in range(longest, 0, -1):
        phrase = " ".join(token for token, _ in run[index : index + size])
        keys = _phrase_keys(phrase)
        if sentence_initial and any(key in COMMON_WORD_NAMES for key in keys):
            continue
        ambiguous = False
        for key in keys:
            places = _unique_places(aliases.get(key, []))
            places = [
                place for place in places if place.place_type != Place.PlaceType.STATE
            ]
            places = _apply_state_prior(places, state)
            places = prefer_settlement_types(places)
            if not places:
                continue
            if _choose_place(places) is not None:
                return False, size
            ambiguous = True
        if ambiguous:
            return True, size
    return False, 0


def _regenerate_llm_places(
    thread: Thread, mentions: list[Mention], state: str | None
) -> None:
    """Replace every mention's LLM rows with a fresh model classification.

    LLM output is thread-level, so new rows attach to the thread's post
    mentions (or the first mention when a thread has no post). Existing
    deterministic and manual rows win any ``(mention, place)`` collision.
    """
    result = classify_with_llm(thread)
    targets = [m for m in mentions if m.kind == Mention.Kind.POST] or mentions[:1]

    with transaction.atomic():
        PlaceMention.objects.filter(
            mention__in=mentions, method=PlaceMention.Method.LLM
        ).delete()
        if result is None or not targets:
            return

        taken = set(
            PlaceMention.objects.filter(mention__in=targets).values_list(
                "mention_id", "place_id"
            )
        )
        rows: list[PlaceMention] = []
        for item in result.places:
            place = resolve_place_phrase(item.name, state=item.state or state)
            if place is None:
                continue
            for mention in targets:
                key = (mention.pk, place.pk)
                if key in taken:
                    continue
                taken.add(key)
                rows.append(
                    PlaceMention(
                        mention=mention,
                        place=place,
                        matched_text=item.name[:255],
                        method=PlaceMention.Method.LLM,
                        confidence=PlaceMention.Confidence.MEDIUM,
                        is_subject=item.role == "subject",
                    )
                )
        if rows:
            PlaceMention.objects.bulk_create(rows)


def _thread_place_role(mention: Mention, place_mention: PlaceMention) -> str:
    """Return the thread role for one mention-level extraction row.

    An operator's or LLM's explicit ``is_subject`` outranks the deterministic
    kind rules.  Otherwise a post is a subject, and a comment is a subject only
    for a governing-body pattern.
    """
    if place_mention.method in {
        PlaceMention.Method.LLM,
        PlaceMention.Method.MANUAL,
    }:
        return (
            ThreadPlace.Role.SUBJECT
            if place_mention.is_subject
            else ThreadPlace.Role.MENTIONED
        )
    if (
        mention.kind == Mention.Kind.POST
        or place_mention.method == PlaceMention.Method.BODY_PATTERN
    ):
        return ThreadPlace.Role.SUBJECT
    return ThreadPlace.Role.MENTIONED


def _merge_thread_place(
    candidates: dict[object, _ThreadPlaceCandidate],
    candidate: _ThreadPlaceCandidate,
) -> None:
    """Keep the strongest proposal for each place.

    Role wins over confidence, so a low-confidence subject is not displaced by
    a high-confidence mention of the same place.  Ties keep the first proposal,
    which is the subreddit before a post and a post before a comment.
    """
    current = candidates.get(candidate.place_id)
    if current is None:
        candidates[candidate.place_id] = candidate
        return
    if current.role != candidate.role:
        if candidate.role == ThreadPlace.Role.SUBJECT:
            candidates[candidate.place_id] = candidate
        return
    if _CONFIDENCE_RANK[candidate.confidence] > _CONFIDENCE_RANK[current.confidence]:
        candidates[candidate.place_id] = candidate


def resolve_place_phrase(
    phrase: str,
    *,
    state: str | None = None,
    required_place_type: str | None = None,
) -> Place | None:
    """Resolve a text phrase to an existing place, or return ``None``.

    Aliases are matched by their normalized key. A matching ``state`` is a
    strong prior: when any candidate is in that state, others are discarded.
    Remaining candidates are ranked by population and only resolve when the
    leader is at least :data:`AMBIGUITY_RATIO` times the runner-up. A CDP with a
    known county rolls up to that county because a CDP has no government of its
    own.
    """
    keys = _phrase_keys(phrase)
    if not keys:
        return None
    aliases = PlaceAlias.objects.filter(key__in=keys).select_related(
        "place", "place__county"
    )
    by_key: dict[str, list[Place]] = {}
    for alias in aliases:
        by_key.setdefault(alias.key, []).append(alias.place)

    for key in keys:
        place = _resolve_candidates(
            phrase,
            by_key.get(key, []),
            state=state,
            required_place_type=required_place_type,
        )
        if place is not None:
            return place
    return None


def _load_aliases(phrases: list[str]) -> dict[str, list[Place]]:
    """Return alias places keyed by normalized phrase for one pattern's hits."""
    keys = {
        key
        for phrase in phrases
        for variant in _phrase_variants(phrase)
        for key in _phrase_keys(variant)
    }
    keys.discard("")
    if not keys:
        return {}

    aliases = PlaceAlias.objects.filter(key__in=keys).select_related(
        "place", "place__county"
    )
    by_key: dict[str, list[Place]] = {}
    for alias in aliases:
        by_key.setdefault(alias.key, []).append(alias.place)
    return by_key


def _phrase_keys(phrase: str) -> list[str]:
    """Return matching keys for a phrase, most exact first.

    Possessives and hyphenated compounds describe a place without being part
    of its name: ``Chico's`` and ``Chico-based`` must also try ``chico``. The
    full normalized key is tried first so genuine hyphenated names such as
    ``Winston-Salem`` still match as a whole.
    """
    variants = [phrase, _POSSESSIVE_RE.sub("", phrase)]
    if not any(character.isspace() for character in phrase):
        variants.extend(_HYPHEN_RE.split(phrase))
    keys: list[str] = []
    for variant in variants:
        key = normalize_key(variant)
        if key and key not in keys:
            keys.append(key)
    return keys


def _is_sentence_initial(text: str, start: int) -> bool:
    """Return whether ``start`` begins a sentence or line."""
    before = text[:start].rstrip(" \t\r")
    while before and before[-1] in _CLOSING_PUNCTUATION:
        before = before[:-1].rstrip(" \t\r")
    return not before or before[-1] in _SENTENCE_BREAKS


def _capitalized_runs(text: str) -> list[list[tuple[str, int]]]:
    """Return runs of contiguous capitalized words with their offsets."""
    runs: list[list[tuple[str, int]]] = []
    current: list[tuple[str, int]] = []
    previous_end: int | None = None

    for token in _CAPITALIZED_TOKEN.finditer(text):
        word = token.group()
        if not word[:1].isupper():
            # Mixed-case brands such as "iPhone" and "eBay" are not places.
            continue
        if previous_end is not None and not text[previous_end : token.start()].strip():
            current.append((word, token.start()))
        else:
            if current:
                runs.append(current)
            current = [(word, token.start())]
        previous_end = token.end()

    if current:
        runs.append(current)
    return runs


def _match_longest_phrase(
    run: list[tuple[str, int]],
    index: int,
    *,
    aliases: dict[str, list[Place]],
    state: str | None,
    sentence_initial: bool,
) -> tuple[Place, str, int] | None:
    """Return the longest resolvable phrase starting at ``run[index]``."""
    longest = min(_MAX_PHRASE_WORDS, len(run) - index)
    for size in range(longest, 0, -1):
        phrase = " ".join(token for token, _ in run[index : index + size])
        keys = _phrase_keys(phrase)
        if sentence_initial and any(key in COMMON_WORD_NAMES for key in keys):
            continue
        for key in keys:
            place = _resolve_candidates(
                phrase,
                aliases.get(key, []),
                state=state,
                required_place_type=None,
                roll_up_cdp=False,
                exclude_states=True,
            )
            if place is not None:
                return place, phrase, size
    return None


def _dedupe_extracted(
    results: Iterable[ExtractedPlace],
) -> list[ExtractedPlace]:
    """Collapse duplicate ``(place, is_subject)`` matches, keeping the best."""
    by_key: dict[tuple[object, bool], ExtractedPlace] = {}
    for result in results:
        key = (result.place.pk, result.is_subject)
        current = by_key.get(key)
        if (
            current is None
            or _CONFIDENCE_RANK[result.confidence]
            > _CONFIDENCE_RANK[current.confidence]
        ):
            by_key[key] = result
    return list(by_key.values())


def _dedupe_by_place(
    results: Iterable[ExtractedPlace],
) -> dict[object, ExtractedPlace]:
    """Collapse to one match per place because the model permits only one.

    A subject wins a confidence tie so a body-pattern match is not displaced by
    a phrase match for the same place.
    """
    by_place: dict[object, ExtractedPlace] = {}
    for result in results:
        current = by_place.get(result.place.pk)
        if current is None:
            by_place[result.place.pk] = result
            continue
        if _CONFIDENCE_RANK[result.confidence] > _CONFIDENCE_RANK[
            current.confidence
        ] or (
            result.confidence == current.confidence
            and result.is_subject
            and not current.is_subject
        ):
            by_place[result.place.pk] = result
    return by_place


def _resolve_variants(
    phrase: str,
    *,
    aliases: dict[str, list[Place]],
    state: str | None,
    required_place_type: str,
) -> tuple[Place | None, str]:
    """Resolve ``phrase``, retrying without a leading article if needed."""
    for variant in _phrase_variants(phrase):
        for key in _phrase_keys(variant):
            place = _resolve_candidates(
                variant,
                aliases.get(key, []),
                state=state,
                required_place_type=required_place_type,
            )
            if place is not None:
                return place, variant
    return None, phrase


def _resolve_candidates(
    phrase: str,
    candidates: Iterable[Place],
    *,
    state: str | None,
    required_place_type: str | None,
    roll_up_cdp: bool = True,
    exclude_states: bool = False,
) -> Place | None:
    """Resolve one phrase against already-loaded alias candidates."""
    key = normalize_key(phrase)
    if not key:
        return None
    if key in COMMON_WORD_NAMES and not phrase[:1].isupper():
        return None

    places = _unique_places(candidates)
    places = _filter_place_type(places, required_place_type)
    if exclude_states:
        places = [
            place for place in places if place.place_type != Place.PlaceType.STATE
        ]
    if not places:
        return None

    places = _apply_state_prior(places, state)
    if required_place_type != Place.PlaceType.COUNTY:
        places = prefer_settlement_types(places)
    place = _choose_place(places)
    if place is None:
        return None
    if not roll_up_cdp:
        return place
    return _roll_up(place, required_place_type)


def _phrase_variants(phrase: str) -> list[str]:
    """Return every trailing sub-phrase, longest first.

    Greedy patterns can capture a leading preposition or article, so "In
    Beverly Hills" must also try "Beverly Hills" and "Hills".
    """
    words = phrase.split()
    return [" ".join(words[index:]) for index in range(len(words))]


def _unique_places(places: Iterable[Place]) -> list[Place]:
    """Return places de-duplicated by primary key, preserving input order."""
    unique: dict[object, Place] = {}
    for place in places:
        unique[place.pk] = place
    return list(unique.values())


def _filter_place_type(
    places: list[Place], required_place_type: str | None
) -> list[Place]:
    """Return only places allowed by ``required_place_type``."""
    if required_place_type is None:
        return places
    if required_place_type == "government":
        return [
            place
            for place in places
            if place.is_government or place.place_type in GOVERNMENT_TYPES
        ]
    if required_place_type == "municipality":
        allowed: frozenset[str] = MUNICIPAL_TYPES
    else:
        allowed = frozenset({required_place_type})
    return [place for place in places if place.place_type in allowed]


def _apply_state_prior(places: list[Place], state: str | None) -> list[Place]:
    """Narrow to the requested state when any candidate is in it."""
    if not state:
        return places
    code = state.strip().upper()
    in_state = [place for place in places if place.state == code]
    return in_state or places


def _choose_place(places: list[Place]) -> Place | None:
    """Choose a single place by the population ambiguity rule.

    Unknown or zero populations are not evidence of a 10x difference, so a
    comparison only resolves when both candidate populations are known and
    nonzero.
    """
    if len(places) == 1:
        return places[0]

    ranked = sorted(places, key=lambda place: place.population or 0, reverse=True)
    top_population = ranked[0].population or 0
    next_population = ranked[1].population or 0
    if (
        top_population
        and next_population
        and top_population >= AMBIGUITY_RATIO * next_population
    ):
        return ranked[0]
    return None


def _roll_up(place: Place, required_place_type: str | None) -> Place:
    """Roll a CDP up to its county unless a CDP was explicitly requested."""
    if (
        required_place_type != Place.PlaceType.CDP
        and place.place_type == Place.PlaceType.CDP
    ):
        county = place.county
        if county is not None:
            return county
    return place
