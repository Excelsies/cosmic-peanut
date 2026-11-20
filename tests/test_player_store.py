"""player.json: the queue and where the dialog starts, written atomically."""

import json
from pathlib import Path

from tidalviz.player.queue import MAX_QUEUE
from tidalviz.player.store import QueueStore, StoredQueue


def files(root: Path, *names: str) -> list[Path]:
    out = [root / n for n in names]
    for p in out:
        p.write_bytes(b"x")
    return out


def test_a_missing_file_loads_as_an_empty_queue(tmp_path: Path):
    assert QueueStore(tmp_path / "player.json").load() == StoredQueue([], 0, None)


def test_save_then_load_round_trips_paths_index_and_folder(tmp_path: Path):
    a, b, c = files(tmp_path, "a.mp3", "b.mp3", "c.mp3")
    store = QueueStore(tmp_path / "state" / "player.json")
    store.save(StoredQueue([a, b, c], 2, str(tmp_path)))
    assert store.load() == StoredQueue([a, b, c], 2, str(tmp_path))


def test_the_file_holds_only_paths_index_and_folder(tmp_path: Path):
    (a,) = files(tmp_path, "a.mp3")
    store = QueueStore(tmp_path / "player.json")
    store.save(StoredQueue([a], 0, None))
    assert json.loads((tmp_path / "player.json").read_text()) == {
        "version": 1,
        "paths": [str(a)],
        "index": 0,
        "lastFolder": None,
    }


def test_files_that_no_longer_exist_are_dropped_and_the_index_follows(tmp_path: Path):
    a, _, c, d = files(tmp_path, "a.mp3", "b.mp3", "c.mp3", "d.mp3")
    (tmp_path / "b.mp3").unlink()
    store = QueueStore(tmp_path / "player.json")
    store.save(StoredQueue([a, tmp_path / "b.mp3", c, d], 3, None))
    assert store.load().paths == [a, c, d] and store.load().index == 2  # still d
    store.save(StoredQueue([a, tmp_path / "b.mp3", c, d], 1, None))  # the current one is gone
    assert store.load().index == 1  # the next survivor, c


def test_an_index_past_the_end_after_pruning_is_clamped(tmp_path: Path):
    (a,) = files(tmp_path, "a.mp3")
    store = QueueStore(tmp_path / "player.json")
    store.save(StoredQueue([a, tmp_path / "gone.mp3"], 1, None))
    assert store.load() == StoredQueue([a], 0, None)


def test_a_corrupt_file_is_set_aside_and_loads_empty(tmp_path: Path):
    p = tmp_path / "player.json"
    p.write_text("{not json")
    assert QueueStore(p).load() == StoredQueue([], 0, None)
    assert not p.exists() and list(tmp_path.glob("player.json.corrupt-*"))


def test_unexpected_shapes_are_ignored_field_by_field(tmp_path: Path):
    (a,) = files(tmp_path, "a.mp3")
    p = tmp_path / "player.json"
    p.write_text(json.dumps({"paths": [str(a), 5, None], "index": "x", "lastFolder": 3}))
    assert QueueStore(p).load() == StoredQueue([a], 0, None)
    p.write_text(json.dumps(["not", "an", "object"]))
    assert QueueStore(p).load() == StoredQueue([], 0, None)


def test_a_huge_file_is_cut_to_the_queue_limit(tmp_path: Path):
    p = tmp_path / "player.json"
    p.write_text(json.dumps({"paths": [str(tmp_path / f"{i}.mp3") for i in range(MAX_QUEUE + 3)]}))
    (tmp_path / "0.mp3").write_bytes(b"x")
    assert QueueStore(p).load().paths == [tmp_path / "0.mp3"]  # only survivors, within the cap


def test_deeply_nested_json_is_corruption_not_a_crash(tmp_path: Path):
    p = tmp_path / "player.json"
    p.write_text("[" * 200000)
    assert QueueStore(p).load() == StoredQueue([], 0, None)
    assert not p.exists() and list(tmp_path.glob("player.json.corrupt-*"))
