"""Terminal-safe text helpers for untrusted crystallographic labels."""

from __future__ import annotations

import unicodedata


def terminal_text(value: object) -> str:
    """Remove terminal-control and bidi characters from display text.

    CIF labels are source data. They must never be allowed to form ANSI/OSC
    escapes before a frame reaches ``Text.from_ansi()`` or a user's terminal.
    Printable Unicode, including chemical superscripts, remains intact.
    """
    return "".join(
        character
        for character in str(value)
        if _is_terminal_safe(character)
    )


def _is_terminal_safe(character: str) -> bool:
    codepoint = ord(character)
    if codepoint == 0x7F or codepoint < 0x20 or 0x80 <= codepoint <= 0x9F:
        return False
    category = unicodedata.category(character)
    if category == "Cc" or category == "Cf":
        return False
    return True


__all__ = ["terminal_text"]