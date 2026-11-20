"""Finding audio files: allowlist, recursion, natural order."""

import os
from pathlib import Path

from tidalviz.player.library import AUDIO_EXTENSIONS
from tidalviz.player.library import collect_audio_files as collect_with_flag


def collect_audio_files(paths, limit=None):
    return (
        collect_with_flag(paths) if limit is None else collect_with_flag(paths, limit=limit)
    ).files


def touch(root: Path, *names: str) -> None:
    for n in names:
        p = root / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


def rel(root: Path, found: list[Path]) -> list[str]:
    return [p.relative_to(root).as_posix() for p in found]


def test_a_folder_is_walked_recursively_keeping_only_the_allowlist(tmp_path: Path):
    touch(
        tmp_path, "a.mp3", "b.FLAC", "cover.jpg", "notes.txt", "x/y/c.ogg", "x/d.exe", ".hid/e.opus"
    )
    assert rel(tmp_path, collect_audio_files([tmp_path])) == [
        ".hid/e.opus",
        "a.mp3",
        "b.FLAC",
        "x/y/c.ogg",
    ]


def test_the_order_is_natural_by_path_not_lexicographic(tmp_path: Path):
    touch(tmp_path, "t10.mp3", "t2.mp3", "t1.mp3", "B/01.mp3", "a/02.mp3", "a/10.mp3", "a/9.mp3")
    assert rel(tmp_path, collect_audio_files([tmp_path])) == [
        "a/02.mp3",
        "a/9.mp3",
        "a/10.mp3",
        "B/01.mp3",
        "t1.mp3",
        "t2.mp3",
        "t10.mp3",
    ]


def test_explicit_files_are_kept_in_the_order_given_without_the_filter(tmp_path: Path):
    touch(tmp_path, "z.mp3", "a.bin")
    got = collect_audio_files([tmp_path / "z.mp3", tmp_path / "a.bin"])
    assert rel(tmp_path, got) == ["z.mp3", "a.bin"]  # the preflight judges them, not the name


def test_files_and_folders_mix(tmp_path: Path):
    touch(tmp_path, "d/2.mp3", "d/1.mp3", "solo.wav")
    got = collect_audio_files([tmp_path / "solo.wav", tmp_path / "d"])
    assert rel(tmp_path, got) == ["solo.wav", "d/1.mp3", "d/2.mp3"]


def test_a_path_that_does_not_exist_is_dropped_and_logged(tmp_path: Path, caplog):
    assert collect_audio_files([tmp_path / "nope"]) == []
    assert "nope" in caplog.text


def test_symlinked_folders_are_not_followed(tmp_path: Path):
    touch(tmp_path, "real/a.mp3")
    (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
    assert rel(tmp_path, collect_audio_files([tmp_path])) == ["real/a.mp3"]


def test_the_allowlist_covers_the_common_formats():
    assert {
        ".mp3",
        ".m4a",
        ".aac",
        ".flac",
        ".ogg",
        ".oga",
        ".opus",
        ".wav",
        ".wma",
        ".aiff",
    } <= set(AUDIO_EXTENSIONS)
    assert all(e == e.lower() and e.startswith(".") for e in AUDIO_EXTENSIONS)


def test_the_walk_stops_at_its_bound_and_says_so(tmp_path: Path):
    touch(tmp_path, *[f"d{i // 10}/t{i}.mp3" for i in range(50)])
    got = collect_with_flag([tmp_path], limit=20)
    assert got.truncated and len(got.files) == 20
    whole = collect_with_flag([tmp_path], limit=50)
    assert not whole.truncated and len(whole.files) == 50  # exactly at the bound is not over it


def test_the_bound_is_a_few_times_the_queue_cap():
    from tidalviz.player.library import WALK_LIMIT
    from tidalviz.player.queue import MAX_QUEUE

    assert MAX_QUEUE < WALK_LIMIT <= 5 * MAX_QUEUE


def test_a_walk_over_a_tree_with_little_audio_stops_at_the_entry_cap(tmp_path: Path):
    touch(tmp_path, "a.mp3", *[f"junk/j{i:03}.txt" for i in range(40)], "z.mp3")
    got = collect_with_flag([tmp_path], entry_limit=10)
    assert got.truncated
    assert rel(tmp_path, got.files) == ["a.mp3"]  # what was kept is a prefix in natural order
    whole = collect_with_flag([tmp_path], entry_limit=1000)
    assert not whole.truncated and rel(tmp_path, whole.files) == ["a.mp3", "z.mp3"]


def test_the_entry_cap_is_shared_by_all_the_folders_of_one_open(tmp_path: Path):
    for d in ("one", "two"):
        touch(tmp_path, *[f"{d}/f{i}.txt" for i in range(8)], f"{d}/s.mp3")
    got = collect_with_flag([tmp_path / "one", tmp_path / "two"], entry_limit=12)
    assert got.truncated and rel(tmp_path, got.files) == ["one/s.mp3"]


def test_the_entry_cap_is_a_named_bound_well_above_the_file_bound():
    from tidalviz.player.library import ENTRY_LIMIT, WALK_LIMIT

    assert ENTRY_LIMIT >= 10 * WALK_LIMIT


def test_unreadable_and_dangling_entries_are_skipped_and_the_walk_goes_on(tmp_path: Path):

    touch(tmp_path, "a.mp3", "d/inside.mp3", "m.mp3")
    locked = tmp_path / "b-locked"
    locked.mkdir()
    (locked / "hidden.mp3").write_bytes(b"x")
    (tmp_path / "c-dangling.mp3").symlink_to(tmp_path / "nowhere.mp3")
    (tmp_path / "c-dangling-dir").symlink_to(tmp_path / "nowhere", target_is_directory=True)
    os.chmod(locked, 0)
    try:
        got = collect_with_flag([tmp_path])
    finally:
        os.chmod(locked, 0o755)
    assert rel(tmp_path, got.files) == [
        "a.mp3",
        "d/inside.mp3",
        "m.mp3",
    ]  # good files on both sides
    assert not got.truncated


def test_an_entry_that_raises_on_inspection_is_skipped(tmp_path: Path, monkeypatch):
    touch(tmp_path, "a.mp3", "b.mp3", "c.mp3")

    class Bad:
        name = "b.mp3"
        path = str(tmp_path / "b.mp3")

        def is_dir(self, follow_symlinks=True):
            raise PermissionError("denied")

        def is_file(self):
            raise PermissionError("denied")

    import tidalviz.player.library as lib

    original = lib._entries

    def entries(folder, budget):
        return [Bad() if e.name == "b.mp3" else e for e in original(folder, budget)]

    monkeypatch.setattr(lib, "_entries", entries)
    assert rel(tmp_path, collect_with_flag([tmp_path]).files) == ["a.mp3", "c.mp3"]


def test_one_huge_directory_is_not_listed_in_full(tmp_path: Path, monkeypatch):
    import tidalviz.player.library as lib

    touch(tmp_path, *[f"f{i:03}.mp3" for i in range(200)])
    read = [0]
    real = os.scandir

    class Counting:
        def __init__(self, it):
            self.it = it

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.it.__exit__(*a)

        def __iter__(self):
            return self

        def __next__(self):
            entry = next(self.it)
            read[0] += 1
            return entry

    monkeypatch.setattr(lib.os, "scandir", lambda p: Counting(real(p)))
    got = collect_with_flag([tmp_path], entry_limit=10)
    assert got.truncated and read[0] <= 11 and len(got.files) <= 10
