"""Utility functions for the meetings app."""


def truncate_text(text: str, max_length: int = 200) -> str:
    """
    Truncate text to max_length characters and add ellipsis if needed.

    Args:
        text: The text to truncate
        max_length: Maximum length before truncation (default: 200)

    Returns:
        Truncated text with "..." appended if truncated, otherwise original text
    """
    if len(text) > max_length:
        return text[:max_length] + "..."
    return text
