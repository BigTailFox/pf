from __future__ import annotations


def _without_box_drawing(text: str) -> str:
    return "".join(
        " " if 0x2500 <= ord(character) <= 0x257F else character
        for character in text
    )


def visible_cli_text(text: str) -> str:
    """Flatten Rich canvas chrome so semantic substrings survive wrapping.

    Panel and table rendering insert box-drawing characters and newlines
    between words. Public-copy assertions should compare this flattening
    instead of a particular terminal width.
    """

    return " ".join(_without_box_drawing(text).split())


def compact_cli_text(text: str) -> str:
    """Remove whitespace and box-drawing so identifiers survive wrapping."""

    return "".join(
        character
        for character in _without_box_drawing(text)
        if not character.isspace()
    )
