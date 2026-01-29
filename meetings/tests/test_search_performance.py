"""Tests for search performance optimizations."""

import pytest

from meetings.views import _get_smart_threshold, _parse_websearch_query


class TestWebsearchQueryParsing:
    """Test parsing of websearch queries to extract tokens."""

    def test_simple_single_word(self):
        """Single word query returns that word as token."""
        tokens, original = _parse_websearch_query("ice")
        assert tokens == ["ice"]
        assert original == "ice"

    def test_multiple_words(self):
        """Multiple words without operators."""
        tokens, original = _parse_websearch_query("affordable housing")
        assert sorted(tokens) == ["affordable", "housing"]
        assert original == "affordable housing"

    def test_quoted_phrase(self):
        """Quoted phrase should be kept as single token."""
        tokens, original = _parse_websearch_query('"affordable housing"')
        assert tokens == ["affordable housing"]
        assert original == '"affordable housing"'

    def test_quoted_phrase_with_or(self):
        """Quoted phrase with OR operator."""
        tokens, original = _parse_websearch_query('"ICE" OR immigration')
        # Order doesn't matter for threshold calculation
        assert set(tokens) == {"ICE", "immigration"}
        assert original == '"ICE" OR immigration'

    def test_and_operator(self):
        """AND operator should be filtered out."""
        tokens, original = _parse_websearch_query("affordable housing AND rent")
        assert sorted(tokens) == ["affordable", "housing", "rent"]
        assert original == "affordable housing AND rent"

    def test_not_operator(self):
        """NOT operator should be filtered out."""
        tokens, original = _parse_websearch_query("housing NOT expensive")
        assert sorted(tokens) == ["expensive", "housing"]
        assert original == "housing NOT expensive"

    def test_multiple_quoted_phrases(self):
        """Multiple quoted phrases."""
        tokens, original = _parse_websearch_query(
            '"immigration enforcement" OR "customs enforcement"'
        )
        assert set(tokens) == {"immigration enforcement", "customs enforcement"}
        assert original == '"immigration enforcement" OR "customs enforcement"'

    def test_mixed_quoted_and_unquoted(self):
        """Mix of quoted and unquoted terms."""
        tokens, original = _parse_websearch_query('"ICE" AND immigration')
        assert set(tokens) == {"ICE", "immigration"}
        assert original == '"ICE" AND immigration'

    def test_case_insensitive_operators(self):
        """Operators should be recognized regardless of case."""
        tokens, original = _parse_websearch_query("ice or rent")
        assert set(tokens) == {"ice", "rent"}
        assert original == "ice or rent"

        tokens, original = _parse_websearch_query("ice Or rent")
        assert set(tokens) == {"ice", "rent"}

    def test_empty_query(self):
        """Empty query should return empty token list."""
        tokens, original = _parse_websearch_query("")
        assert tokens == []
        assert original == ""

    def test_only_operators(self):
        """Query with only operators should return empty list."""
        tokens, original = _parse_websearch_query("OR AND NOT")
        assert tokens == []
        assert original == "OR AND NOT"


class TestSmartThreshold:
    """Test smart threshold calculation based on token length."""

    def test_very_short_tokens(self):
        """2 characters or less gets highest threshold."""
        assert _get_smart_threshold(["to"]) == 0.02
        assert _get_smart_threshold(["be"]) == 0.02
        assert _get_smart_threshold(["or"]) == 0.02

    def test_short_tokens(self):
        """3 character tokens get moderate threshold."""
        assert _get_smart_threshold(["ice"]) == 0.015
        assert _get_smart_threshold(["law"]) == 0.015
        assert _get_smart_threshold(["ada"]) == 0.015

    def test_medium_short_tokens(self):
        """4 character tokens get normal threshold."""
        assert _get_smart_threshold(["rent"]) == 0.01
        assert _get_smart_threshold(["park"]) == 0.01
        assert _get_smart_threshold(["zone"]) == 0.01

    def test_normal_length_tokens(self):
        """5+ character tokens get normal threshold."""
        assert _get_smart_threshold(["housing"]) == 0.01
        assert _get_smart_threshold(["affordable"]) == 0.01
        assert _get_smart_threshold(["immigration"]) == 0.01

    def test_multiple_tokens_uses_shortest(self):
        """With multiple tokens, use shortest for threshold."""
        # "ice" (3 chars) is shortest
        assert _get_smart_threshold(["ice", "immigration"]) == 0.015

        # "rent" (4 chars) is shortest
        assert _get_smart_threshold(["affordable", "rent", "housing"]) == 0.01

        # "housing" (7 chars) is shortest
        assert _get_smart_threshold(["housing", "affordable"]) == 0.01

    def test_quoted_phrases_treated_as_whole(self):
        """Quoted phrases should be treated as single token."""
        # "affordable housing" is 18 chars
        assert _get_smart_threshold(["affordable housing"]) == 0.01

        # "Immigration and Customs Enforcement" is very long
        assert _get_smart_threshold(["Immigration and Customs Enforcement"]) == 0.01

    def test_empty_token_list(self):
        """Empty token list returns default threshold."""
        assert _get_smart_threshold([]) == 0.01

    def test_mixed_length_tokens(self):
        """Mix of short and long tokens uses shortest."""
        # "or" (2 chars) should dominate
        assert _get_smart_threshold(["or", "affordable", "housing"]) == 0.02

        # "ice" (3 chars) should dominate
        assert _get_smart_threshold(["ice", "rent", "housing"]) == 0.015


class TestIntegratedBehavior:
    """Test integrated parsing + threshold behavior."""

    @pytest.mark.parametrize(
        "query,expected_threshold",
        [
            ("ice", 0.015),  # 3 chars
            ('"ICE"', 0.015),  # 3 chars in quotes
            ('"ICE" OR immigration', 0.015),  # Shortest is 3 chars
            ("affordable housing", 0.01),  # Both long
            ('"affordable housing"', 0.01),  # Long phrase
            ("rent AND housing", 0.01),  # Shortest is 4 chars
            ('"or"', 0.02),  # 2 chars in quotes (otherwise filtered as operator)
            ("law OR rent", 0.015),  # Shortest is 3 chars (law)
            ("housing NOT rent", 0.01),  # Shortest is 4 chars (rent)
        ],
    )
    def test_query_to_threshold(self, query, expected_threshold):
        """Test complete flow from query to threshold."""
        tokens, _ = _parse_websearch_query(query)
        threshold = _get_smart_threshold(tokens)
        assert threshold == expected_threshold, f"Query: {query}, Tokens: {tokens}"
