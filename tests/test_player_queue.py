"""The play queue model: pure Python, no GStreamer."""

from pathlib import Path

import pytest

from tidalviz.player.queue import MAX_QUEUE, PlayQueue
from tidalviz.player.tags import TrackTags


def paths(*names: str) -> list[Path]:
    return [Path("/m") / n for n in names]


def titles(q: PlayQueue) -> list[str]:
    return [t.title for t in q.info()]


def test_a_new_queue_is_empty_with_no_current_track():
    q = PlayQueue()
    assert len(q) == 0 and q.index is None and q.current is None and q.info() == ()


def test_adding_to_an_empty_queue_selects_the_first_track_with_file_name_titles():
    q = PlayQueue()
    q.add(paths("b.mp3", "a.flac"), replace=False)
    assert q.index == 0
    assert titles(q) == ["b", "a"]  # insertion order, titles from file names until probed


def test_appending_keeps_the_current_track():
    q = PlayQueue()
    q.add(paths("a.mp3", "b.mp3"), replace=False)
    q.select(1)
    q.add(paths("c.mp3"), replace=False)
    assert q.index == 1 and titles(q) == ["a", "b", "c"]


def test_replacing_starts_over_at_the_first_new_track():
    q = PlayQueue()
    q.add(paths("a.mp3", "b.mp3"), replace=False)
    q.select(1)
    q.add(paths("x.mp3", "y.mp3"), replace=True)
    assert q.index == 0 and titles(q) == ["x", "y"]


def test_replacing_with_nothing_empties_the_queue():
    q = PlayQueue()
    q.add(paths("a.mp3"), replace=False)
    q.add([], replace=True)
    assert len(q) == 0 and q.index is None


def test_clear_empties_the_queue_and_bumps_the_revision():
    q = PlayQueue()
    q.add(paths("a.mp3"), replace=False)
    before = q.revision
    q.clear()
    assert len(q) == 0 and q.index is None and q.revision > before


def test_select_validates_the_index():
    q = PlayQueue()
    q.add(paths("a.mp3", "b.mp3"), replace=False)
    q.select(1)
    assert q.index == 1
    for bad in (-1, 2, 99):
        with pytest.raises(ValueError):
            q.select(bad)
    assert q.index == 1


def test_next_and_previous_move_and_report_the_ends():
    q = PlayQueue()
    q.add(paths("a.mp3", "b.mp3", "c.mp3"), replace=False)
    assert q.next() and q.index == 1
    assert q.next() and q.index == 2
    assert not q.next() and q.index == 2  # the end: stays put, the caller decides
    assert q.previous() and q.index == 1
    assert q.previous() and q.index == 0
    assert not q.previous() and q.index == 0


def test_next_skips_unplayable_tracks_and_stays_put_when_none_is_left():
    q = PlayQueue()
    q.add(paths("a.mp3", "bad.txt", "worse.txt", "d.mp3"), replace=False)
    q.mark_unplayable(q.tracks[1], "not audio")
    q.mark_unplayable(q.tracks[2], "not audio")
    assert q.next() and q.index == 3
    q.mark_unplayable(q.tracks[3], "gone")
    q.select(0)
    assert not q.next() and q.index == 0


def test_previous_skips_unplayable_tracks():
    q = PlayQueue()
    q.add(paths("a.mp3", "bad.txt", "c.mp3"), replace=False)
    q.mark_unplayable(q.tracks[1], "not audio")
    q.select(2)
    assert q.previous() and q.index == 0


def test_applying_a_probe_fills_tags_and_duration_and_bumps_the_revision():
    q = PlayQueue()
    q.add(paths("a.mp3"), replace=False)
    before = q.revision
    q.apply_probe(q.tracks[0], tags=TrackTags("Band", "Album", "Song"), duration=61.5)
    (info,) = q.info()
    assert (info.artist, info.album, info.title, info.duration) == ("Band", "Album", "Song", 61.5)
    assert info.playable and q.revision > before


def test_marking_unplayable_is_visible_in_the_entries_and_remembers_why():
    q = PlayQueue()
    q.add(paths("a.mp3"), replace=False)
    q.mark_unplayable(q.tracks[0], "no audio stream")
    assert q.info()[0].playable is False
    assert q.tracks[0].reason == "no audio stream"


def test_the_queue_is_capped_and_reports_how_many_were_dropped():
    q = PlayQueue()
    dropped = q.add([Path(f"/m/{i}.mp3") for i in range(MAX_QUEUE + 5)], replace=False)
    assert len(q) == MAX_QUEUE and dropped == 5
    assert q.add(paths("x.mp3"), replace=False) == 1  # already full


def test_restore_keeps_the_index_and_clamps_it():
    q = PlayQueue()
    q.restore(paths("a.mp3", "b.mp3", "c.mp3"), index=2)
    assert q.index == 2
    q.restore(paths("a.mp3"), index=9)
    assert q.index == 0
    q.restore([], index=3)
    assert q.index is None


def test_tracks_dropped_by_a_replace_or_a_clear_are_no_longer_alive():
    q = PlayQueue()
    q.add(paths("a.mp3", "b.mp3"), replace=False)
    old = list(q.tracks)
    assert all(t.alive for t in old)
    q.add(paths("c.mp3"), replace=True)
    assert not any(t.alive for t in old) and q.tracks[0].alive
    kept = q.tracks[0]
    q.clear()
    assert not kept.alive


def test_restore_also_retires_the_old_tracks():
    q = PlayQueue()
    q.add(paths("a.mp3"), replace=False)
    old = q.tracks[0]
    q.restore(paths("b.mp3"), index=0)
    assert not old.alive
