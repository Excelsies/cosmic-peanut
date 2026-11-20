"""Track text from untrusted sources (tags, file names), made safe to show (docs/protocols.md §4)."""

import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_TEXT = 200  # characters per artist / album / title; keeps every queue message bounded
# Control, format (bidi controls, zero-width space, tag characters), line / paragraph separators,
# private use, lone surrogates. Not Cn (unassigned: newer than Python's tables) and not U+200C /
# U+200D, which Persian, Indic scripts and emoji sequences need.
_DROPPED = frozenset({"Cc", "Cf", "Zl", "Zp", "Co", "Cs"})
_KEPT = frozenset({"\u200c", "\u200d"})


@dataclass(frozen=True, slots=True)
class TrackTags:
    artist: str = ""
    album: str = ""
    title: str = ""


def clean_text(value: Any) -> str:
    """Strip surrounding whitespace and characters that can hide or reorder text, cap the length;
    empty means missing."""
    if not isinstance(value, str):
        return ""
    text = "".join(
        c for c in value if c in _KEPT or unicodedata.category(c) not in _DROPPED
    ).strip()
    return text[:MAX_TEXT]


def tags_for(path: Path, *, artist: Any, album: Any, title: Any) -> TrackTags:
    """Tags as shown. A missing title becomes the file name without its extension; a missing
    artist or album stays empty (guessing them from folder names would be wrong too often)."""
    return TrackTags(
        artist=clean_text(artist),
        album=clean_text(album),
        title=clean_text(title) or clean_text(path.stem),
    )
