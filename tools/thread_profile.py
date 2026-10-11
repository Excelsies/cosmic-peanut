"""Per-thread CPU and wakeups of a running Tidalviz, its WebKit children, Hyprland and PipeWire.

Linux only (reads /proc; nothing is attached or injected). Two phases:

1. CPU window: per process and per busy thread, CPU % of one core and voluntary / involuntary
   context switches per second (wakeups).
2. UI runs: polls the Tidalviz main thread (GTK and the WebKit UI process work) and times each
   stretch it spends running. Runs over 1 ms are roughly one per painted frame. The poll keeps
   this tool's own thread busy, so it runs after the CPU window, not during it.

    uv run python -m tools.thread_profile                      # finds the running tidalviz
    uv run python -m tools.thread_profile --pid 12345 --seconds 10 --ui-seconds 3
"""

import argparse
import itertools
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROC = Path("/proc")
SYSTEM = ("Hyprland", "pipewire", "pipewire-pulse", "wireplumber")
UI_RUN_S = 0.001


@dataclass(frozen=True, slots=True)
class ThreadSample:
    comm: str
    ticks: int  # utime + stime, in clock ticks
    voluntary: int
    involuntary: int


@dataclass(frozen=True, slots=True)
class ThreadRate:
    pid: int
    tid: int
    comm: str
    cpu_pct: float  # of one core
    voluntary: float  # per second
    involuntary: float


@dataclass(frozen=True, slots=True)
class UiSummary:
    runs_per_s: float  # runs over UI_RUN_S
    p50_ms: float
    p90_ms: float
    max_ms: float
    run_share: float  # fraction of the window the thread was running (all runs)


def parse_stat(text: str) -> tuple[str, str, int, int]:
    """(comm, state, ppid, utime + stime) from a /proc stat line; comm may hold spaces or ')'."""
    comm = text[text.index("(") + 1 : text.rindex(")")]
    f = text[text.rindex(")") + 2 :].split()
    return comm, f[0], int(f[1]), int(f[11]) + int(f[12])


def parse_switches(text: str) -> tuple[int, int]:
    """(voluntary, involuntary) context switches from a /proc status file."""
    fields = dict(line.split(":", 1) for line in text.splitlines() if "ctxt_switches" in line)
    return int(fields["voluntary_ctxt_switches"]), int(fields["nonvoluntary_ctxt_switches"])


def _pids(proc: Path) -> list[int]:
    return sorted(int(p.name) for p in proc.iterdir() if p.name.isdigit())


def _stat(proc: Path, pid: int) -> tuple[str, str, int, int] | None:
    try:
        return parse_stat((proc / str(pid) / "stat").read_text())
    except (OSError, ValueError, IndexError):  # exited, or not ours to read
        return None


def find_tidalviz(proc: Path = PROC) -> list[int]:
    """Processes started as `tidalviz` (the console script) or `python -m tidalviz`."""
    out = []
    for pid in _pids(proc):
        if pid == os.getpid() or (stat := _stat(proc, pid)) is None:
            continue
        try:
            args = (proc / str(pid) / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        module_run = any(a == b"-m" and b == b"tidalviz" for a, b in itertools.pairwise(args))
        if stat[0] == "tidalviz" or module_run:
            out.append(pid)
    return out


def watched(pid: int, proc: Path = PROC) -> list[int]:
    """`pid`, all its descendants (WebKit's web and network processes, their sandbox), and the
    compositor and audio server it depends on."""
    stats = {p: s for p in _pids(proc) if (s := _stat(proc, p)) is not None}
    keep = {pid} | {p for p, s in stats.items() if s[0] in SYSTEM}
    frontier = {pid}
    while frontier:  # bounded: each pass adds only pids not seen yet
        frontier = {p for p, s in stats.items() if s[2] in frontier and p not in keep}
        keep |= frontier
    return sorted(keep)


def snapshot(pids: list[int], proc: Path = PROC) -> dict[tuple[int, int], ThreadSample]:
    out: dict[tuple[int, int], ThreadSample] = {}
    for pid in pids:
        try:
            tids = [int(t.name) for t in (proc / str(pid) / "task").iterdir()]
        except OSError:
            continue
        for tid in tids:
            task = proc / str(pid) / "task" / str(tid)
            try:
                comm, _, _, ticks = parse_stat((task / "stat").read_text())
                vol, invol = parse_switches((task / "status").read_text())
            except (OSError, ValueError, IndexError, KeyError):
                continue
            out[(pid, tid)] = ThreadSample(comm, ticks, vol, invol)
    return out


def rates(
    before: dict[tuple[int, int], ThreadSample],
    after: dict[tuple[int, int], ThreadSample],
    *,
    seconds: float,
    hz: int,
) -> list[ThreadRate]:
    """Per-thread rates for threads alive at both snapshots."""
    out = []
    for (pid, tid), b in after.items():
        if (a := before.get((pid, tid))) is None:
            continue
        out.append(
            ThreadRate(
                pid,
                tid,
                b.comm,
                100.0 * (b.ticks - a.ticks) / hz / seconds,
                (b.voluntary - a.voluntary) / seconds,
                (b.involuntary - a.involuntary) / seconds,
            )
        )
    return out


class RunTimer:
    """Turns polled run states into runs: stretches of consecutive `R` samples."""

    def __init__(self) -> None:
        self.runs: list[float] = []
        self._start: float | None = None

    def feed(self, t: float, state: str) -> None:
        if state == "R":
            if self._start is None:
                self._start = t
        elif self._start is not None:
            self.runs.append(t - self._start)
            self._start = None


def ui_summary(runs: list[float], *, seconds: float) -> UiSummary:
    share = sum(runs) / seconds
    long = sorted(r for r in runs if r > UI_RUN_S)
    if not long:
        return UiSummary(0.0, 0.0, 0.0, 0.0, share)
    return UiSummary(
        runs_per_s=len(long) / seconds,
        p50_ms=statistics.median(long) * 1e3,
        p90_ms=long[int(0.9 * (len(long) - 1))] * 1e3,
        max_ms=long[-1] * 1e3,
        run_share=share,
    )


def time_ui_runs(pid: int, seconds: float, proc: Path = PROC) -> UiSummary:
    fd = os.open(proc / str(pid) / "task" / str(pid) / "stat", os.O_RDONLY)
    timer = RunTimer()
    try:
        start = time.perf_counter()
        end = start + seconds
        while (t := time.perf_counter()) < end:
            line = os.pread(fd, 512, 0)
            timer.feed(t, chr(line[line.rindex(b")") + 2]))
    finally:
        os.close(fd)
    return ui_summary(timer.runs, seconds=time.perf_counter() - start)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--pid", type=int, help="the tidalviz process (default: find it)")
    ap.add_argument("--seconds", type=float, default=5.0, help="CPU window")
    ap.add_argument("--ui-seconds", type=float, default=3.0, help="UI-run window (0 skips it)")
    args = ap.parse_args()
    if not sys.platform.startswith("linux"):
        sys.exit("thread_profile reads /proc: Linux only")
    pid = args.pid
    if pid is None:
        found = find_tidalviz()
        if len(found) != 1:
            sys.exit(f"found tidalviz pids {found}; pass --pid")
        pid = found[0]
    pids = watched(pid)
    hz = os.sysconf("SC_CLK_TCK")
    before, t0 = snapshot(pids), time.monotonic()
    time.sleep(args.seconds)
    after, dt = snapshot(pids), time.monotonic() - t0
    rows = rates(before, after, seconds=dt, hz=hz)

    print(f"CPU % of one core and wakeups/s over {dt:.1f} s")
    for p in pids:
        mine = [r for r in rows if r.pid == p]
        if mine:
            comm = next((r.comm for r in mine if r.tid == p), mine[0].comm)
            cpu, vol = sum(r.cpu_pct for r in mine), sum(r.voluntary for r in mine)
            print(f"  {p:>7} {comm:<16} {cpu:6.1f}%  {vol:7.0f}/s  ({len(mine)} threads)")
    print("busy threads (> 0.1% or > 20 wakeups/s)")
    for r in sorted(rows, key=lambda r: (r.cpu_pct, r.voluntary), reverse=True)[:30]:
        if r.cpu_pct > 0.1 or r.voluntary > 20:
            print(
                f"  {r.cpu_pct:6.1f}%  vol {r.voluntary:6.0f}/s  invol {r.involuntary:5.0f}/s"
                f"  {r.pid}/{r.tid} {r.comm}"
            )
    if args.ui_seconds > 0:
        ui = time_ui_runs(pid, args.ui_seconds)
        print(
            f"main thread: {ui.runs_per_s:.1f} UI runs/s over 1 ms, p50 {ui.p50_ms:.2f} ms, "
            f"p90 {ui.p90_ms:.2f} ms, max {ui.max_ms:.2f} ms; running {100 * ui.run_share:.1f}%"
        )


if __name__ == "__main__":
    main()
