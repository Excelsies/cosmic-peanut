"""Finding the audio files to queue: an extension allowlist and a natural order."""

import itertools
import logging
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from tidalviz.player.queue import MAX_QUEUE

log = logging.getLogger(__name__)

# What GStreamer's common plugins decode and the discoverer would otherwise spend ~100 ms per
# non-audio file (a cover .jpg) to reject. ALAC lives in .m4a.
AUDIO_EXTENSIONS = (
    ".aac",
    ".aif",
    ".aiff",
    ".flac",
    ".m4a",
    ".mp3",
    ".oga",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
)

WALK_LIMIT = 3 * MAX_QUEUE  # audio files found before a folder walk gives up (queue cap: 2000)
ENTRY_LIMIT = 200_000  # directory entries looked at, audio or not, before a walk gives up

_DIGITS = re.compile(r"(\d+)")


def _part_key(part: str) -> tuple[str | int, ...]:
    """Sort key of one name: digit runs compared as numbers ("2" before "10")."""
    return tuple(int(s) if i % 2 else s.casefold() for i, s in enumerate(_DIGITS.split(part)))


class Collected(NamedTuple):
    files: list[Path]
    truncated: bool  # a bound stopped the walk: there may be more than was collected


def collect_audio_files(
    paths: Sequence[Path], limit: int = WALK_LIMIT, entry_limit: int = ENTRY_LIMIT
) -> Collected:
    """Files to queue. A file is kept as given (the preflight judges it, not its name); a folder
    contributes its audio files, searched recursively without following symlinks in natural path
    order (the walk visits entries in that order, so a cut keeps the first ones).

    Two bounds end the walk, both reported as ``truncated``: ``limit`` audio files in all, and
    ``entry_limit`` directory entries looked at (a home directory or `/` holds millions of
    entries and little audio). Unreadable entries are skipped."""
    out: list[Path] = []
    truncated = False
    budget = [entry_limit]  # entries still allowed, shared by all the folders
    for path in paths:
        if len(out) >= limit or budget[0] <= 0:
            truncated = True
            break
        if path.is_dir():
            found, cut = _walk(path, limit - len(out), budget)
            out.extend(found)
            truncated = truncated or cut
        elif path.is_file():
            out.append(path)
        else:
            log.warning("not a file or folder, skipped: %s", path.name)
    return Collected(out, truncated)


def _entries(folder: Path, budget: list[int]) -> list[os.DirEntry[str]]:
    """The folder's entries in natural order. At most one more than the entry budget is read, so a
    directory of millions of entries is not listed in full; for such a directory the file system
    decides which entries are read (the walk then ends as truncated)."""
    try:
        with os.scandir(folder) as it:
            entries = list(itertools.islice(it, max(budget[0], 0) + 1))
    except OSError as e:
        log.warning("cannot read %s: %s", e.filename, e.strerror)
        return []
    entries.sort(key=lambda e: _part_key(e.name))
    return entries


def _walk(folder: Path, limit: int, budget: list[int]) -> tuple[list[Path], bool]:
    """Audio files under ``folder`` in natural path order, depth first. Returns True as the second
    value when ``limit`` files were found and one more exists, or the entry ``budget`` ran out.
    Symlinked folders are not entered; an entry that cannot be inspected is skipped."""
    found: list[Path] = []
    stack = [iter(_entries(folder, budget))]
    while stack:
        entry = next(stack[-1], None)
        if entry is None:
            stack.pop()
            continue
        if budget[0] <= 0:
            return found, True
        budget[0] -= 1
        try:
            if entry.is_dir(follow_symlinks=False):
                stack.append(iter(_entries(Path(entry.path), budget)))
            elif entry.is_file() and Path(entry.name).suffix.lower() in AUDIO_EXTENSIONS:
                if len(found) >= limit:
                    return found, True
                found.append(Path(entry.path))
        except OSError as e:
            log.warning("cannot inspect %s: %s", entry.name, e.strerror)
    return found, False
