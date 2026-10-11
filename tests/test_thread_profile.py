"""tools.thread_profile: /proc parsing, process discovery and UI-run counting (fixture /proc)."""

from pathlib import Path

import pytest

from tools import thread_profile as tp

# A real WebKitWebProcess stat line, with a comm that holds a space and a parenthesis.
STAT = (
    "4242 (Web (Content)) S 4200 4200 4200 0 -1 4194560 21510 0 0 0 1234 567 0 0 20 0 21 0 "
    "991 3546828800 69546 18446744073709551615 1 1 0 0 0 0 0 4096 0 0 0 0 17 5 0 0 0 0 0\n"
)
STATUS = (
    "Name:\tWebKitWebProces\nState:\tS (sleeping)\nThreads:\t21\n"
    "voluntary_ctxt_switches:\t9001\nnonvoluntary_ctxt_switches:\t77\n"
)


def write_task(proc: Path, pid: int, tid: int, comm: str, state: str, ppid: int, ticks: int):
    task = proc / str(pid) / "task" / str(tid)
    task.mkdir(parents=True, exist_ok=True)
    stat = f"{tid} ({comm}) {state} {ppid} 0 0 0 -1 0 0 0 0 0 {ticks} 0 0 0 20 0 1 0 1 0 0\n"
    (task / "stat").write_text(stat)
    (task / "status").write_text(
        f"voluntary_ctxt_switches:\t{ticks * 10}\nnonvoluntary_ctxt_switches:\t{ticks}\n"
    )
    if tid == pid:
        (proc / str(pid) / "stat").write_text(stat)
        (proc / str(pid) / "comm").write_text(comm + "\n")
        (proc / str(pid) / "cmdline").write_bytes(b"")


@pytest.fixture
def proc(tmp_path: Path) -> Path:
    p = tmp_path / "proc"
    write_task(p, 1, 1, "systemd", "S", 0, 5)
    write_task(p, 100, 100, "tidalviz", "S", 1, 50)
    write_task(p, 100, 101, "asyncio_0", "S", 1, 3)
    write_task(p, 200, 200, "bwrap", "S", 100, 0)
    write_task(p, 201, 201, "WebKitWebProces", "S", 200, 40)
    write_task(p, 300, 300, "WebKitNetworkPr", "S", 100, 2)
    write_task(p, 400, 400, "Hyprland", "S", 1, 30)
    write_task(p, 500, 500, "pipewire", "S", 1, 8)
    write_task(p, 600, 600, "python3", "S", 1, 1)
    (p / "600" / "cmdline").write_bytes(b"python3\0-m\0tidalviz\0--dev\0")
    (p / "self").mkdir()
    return p


def test_stat_line_is_parsed_past_a_comm_with_spaces_and_parens() -> None:
    assert tp.parse_stat(STAT) == ("Web (Content)", "S", 4200, 1234 + 567)


def test_context_switches_are_read_from_status() -> None:
    assert tp.parse_switches(STATUS) == (9001, 77)


def test_finds_tidalviz_by_comm_or_module_run(proc: Path) -> None:
    assert tp.find_tidalviz(proc) == [100, 600]


def test_watched_processes_are_tidalviz_its_descendants_hyprland_and_pipewire(proc: Path) -> None:
    assert tp.watched(100, proc) == [100, 200, 201, 300, 400, 500]


def test_rates_are_per_second_and_skip_threads_that_came_and_went(proc: Path) -> None:
    before = tp.snapshot([100], proc)
    write_task(proc, 100, 100, "tidalviz", "R", 1, 70)  # +20 ticks, +200 vol, +20 invol
    write_task(proc, 100, 102, "late", "S", 1, 9)
    after = tp.snapshot([100], proc)
    rows = {r.tid: r for r in tp.rates(before, after, seconds=2.0, hz=100)}
    assert set(rows) == {100, 101}
    main = rows[100]
    assert (main.cpu_pct, main.voluntary, main.involuntary) == (10.0, 100.0, 10.0)
    assert rows[101].cpu_pct == 0.0


def test_runs_are_contiguous_running_samples() -> None:
    runs = tp.RunTimer()
    states = [(0.000, "S"), (0.010, "R"), (0.015, "R"), (0.0155, "S"), (0.020, "R"), (0.0204, "D")]
    for t, s in states:
        runs.feed(t, s)
    assert runs.runs == pytest.approx([0.0055, 0.0004])


def test_ui_summary_counts_runs_over_one_millisecond() -> None:
    summary = tp.ui_summary([0.004, 0.0046, 0.0005, 0.010], seconds=2.0)
    assert summary.runs_per_s == 1.5  # three runs over 1 ms in 2 s
    assert summary.p50_ms == pytest.approx(4.6)
    assert summary.max_ms == pytest.approx(10.0)
    assert summary.run_share == pytest.approx(0.00955)  # all runs: the main thread is busy 1%
