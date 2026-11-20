"""Handing files to an already running instance (docs/protocols.md §6).

``tidalviz FILE`` while Tidalviz runs on the same data dir sends the absolute paths to it over a
Unix domain socket and exits; the running instance opens them. The socket belongs to the user and
is keyed by the data dir (so `--home` instances and the tests never meet the real one):

    $XDG_RUNTIME_DIR/tidalviz/<16 hex of sha256(data dir)>.sock    (else <tmp>/tidalviz-<uid>/…)

Everything a client sends is untrusted: one JSON line, bounded in size and count, absolute path
strings only, read under a deadline. A bad client costs one connection, never the listener.
"""

import contextlib
import fcntl
import hashlib
import json
import logging
import os
import socket
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

log = logging.getLogger(__name__)

PROTOCOL = 1
MAX_MESSAGE_BYTES = 1 << 20
MAX_PATHS = 1000
MAX_ACCEPT_FAILURES = 50  # consecutive accept() errors before the listener gives up
MAX_PATH_CHARS = 4096
ACK_TIMEOUT_S = 3.0  # a sender waits this long for the listener's answer
READ_TIMEOUT_S = 2.0  # a listener waits this long, in all, for one message
MAX_SOCKET_PATH = 100  # sun_path holds 104 bytes (macOS) or 108 (Linux), with its NUL

OnPaths = Callable[[list[Path]], None]


@dataclass(frozen=True, slots=True)
class Env:
    """Where per-user runtime files may go (injectable so tests never touch the real ones)."""

    runtime_dir: Path | None  # $XDG_RUNTIME_DIR (Linux); None when unset (macOS)
    tmp_dir: Path
    uid: int

    @classmethod
    def from_os(cls) -> "Env":
        run = os.environ.get("XDG_RUNTIME_DIR", "")
        return cls(
            Path(run) if run and os.path.isabs(run) else None,
            Path(tempfile.gettempdir()),
            os.getuid(),
        )


@dataclass(frozen=True, slots=True)
class Acquired:
    listener: "Listener | None"  # we are the instance others hand their files to
    handed_over: bool  # our files went to the running instance: exit without a window


def _key(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]


def socket_path(root: Path, env: Env) -> Path:
    """The socket for the instance on this data dir: short enough for ``sun_path``."""
    name = f"{_key(root)}.sock"
    candidates = [env.tmp_dir / f"tidalviz-{env.uid}"]
    if env.runtime_dir is not None:
        candidates.insert(0, env.runtime_dir / "tidalviz")
    for d in candidates:
        if len(os.fsencode(d / name)) < MAX_SOCKET_PATH:
            return d / name
    return candidates[-1] / name


def _private_dir(d: Path, uid: int) -> bool:
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid or st.st_mode & 0o077:
        log.warning("not handing files over: %s is not a private directory of ours", d)
        return False
    return True


@contextlib.contextmanager
def _locked(path: Path) -> Generator[None]:
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing releases the lock


def encode(paths: Sequence[Path]) -> bytes:
    return json.dumps({"v": PROTOCOL, "paths": [str(p) for p in paths]}).encode() + b"\n"


def decode(line: bytes) -> list[Path] | None:
    """The paths of a well-formed message, else None."""
    try:
        loaded: Any = json.loads(line)
    except (ValueError, RecursionError):
        return None
    if not isinstance(loaded, dict):
        return None
    msg = cast(dict[str, Any], loaded)
    if msg.get("v") != PROTOCOL:
        return None
    paths = msg.get("paths")
    if not isinstance(paths, list) or not 0 < len(cast(list[Any], paths)) <= MAX_PATHS:
        return None
    out: list[Path] = []
    for p in cast(list[Any], paths):
        if not isinstance(p, str) or not p.startswith("/") or len(p) > MAX_PATH_CHARS or "\0" in p:
            return None
        out.append(Path(p))
    return out


def _send(path: Path, paths: Sequence[Path], ack_timeout: float) -> bool:
    """True when the listener at ``path`` accepted the files."""
    data = encode(paths)
    if len(data) > MAX_MESSAGE_BYTES:
        return False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(ack_timeout)
        s.connect(str(path))
        s.sendall(data)
        return s.recv(16).startswith(b"ok")


class Listener:
    def __init__(self, sock: socket.socket, path: Path) -> None:
        self.path = path
        self._sock = sock
        self._inode = os.lstat(path).st_ino
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def serve(
        self, on_paths: OnPaths, *, read_timeout: float = READ_TIMEOUT_S, retry_delay: float = 0.1
    ) -> None:
        """Start answering clients; ``on_paths`` runs on the listener's thread."""
        self._thread = threading.Thread(
            target=self._run,
            args=(on_paths, read_timeout, retry_delay),
            name="tidalviz-instance",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2.0)
        self._sock.close()
        with contextlib.suppress(OSError):
            if os.lstat(self.path).st_ino == self._inode:  # still ours
                os.unlink(self.path)

    def _run(self, on_paths: OnPaths, read_timeout: float, retry_delay: float) -> None:
        self._sock.settimeout(0.2)
        failures = 0
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError as e:
                if self._stop.is_set():
                    return
                failures += 1
                if failures == 1:
                    log.warning("instance socket: accept failed: %s", e)
                if failures >= MAX_ACCEPT_FAILURES:
                    log.warning("instance socket: giving up after %d failures in a row", failures)
                    return  # a later launch finds nobody listening and starts normally
                time.sleep(retry_delay)  # do not spin on a persistent error
                continue
            failures = 0
            with conn:
                try:
                    paths = self._read(conn, read_timeout)
                    conn.sendall(b"ok\n" if paths else b"error\n")
                except OSError as e:
                    log.info("dropped a client: %s", e)
                    continue
            if paths:
                try:
                    on_paths(paths)
                except Exception:
                    log.exception("opening handed-over files failed")

    @staticmethod
    def _read(conn: socket.socket, read_timeout: float) -> list[Path] | None:
        deadline = time.monotonic() + read_timeout
        buf = b""
        while b"\n" not in buf:
            left = deadline - time.monotonic()
            if left <= 0 or len(buf) > MAX_MESSAGE_BYTES:
                return None
            conn.settimeout(left)
            chunk = conn.recv(65536)
            if not chunk:
                return None
            buf += chunk
        line = buf.split(b"\n", 1)[0]
        return decode(line) if len(line) <= MAX_MESSAGE_BYTES else None


def acquire(
    root: Path,
    paths: Sequence[Path] | None,
    env: Env | None = None,
    *,
    ack_timeout: float = ACK_TIMEOUT_S,
) -> Acquired:
    """Become the instance on ``root``, or hand ``paths`` to the one that already is.

    Check and bind happen under a file lock, so launches racing each other cannot both think
    they are first. Anything unusual (an unsafe directory, a foreign file, a listener that does
    not answer) means no listener and no hand-over: the caller just starts normally.
    """
    env = env or Env.from_os()
    path = socket_path(root, env)
    try:
        if not _private_dir(path.parent, env.uid):
            return Acquired(None, False)
        with _locked(path.with_suffix(".lock")):
            return _claim(path, paths, ack_timeout)
    except OSError as e:
        log.warning("single-instance hand-over unavailable: %s", e)
        return Acquired(None, False)


def _claim(path: Path, paths: Sequence[Path] | None, ack_timeout: float) -> Acquired:
    if os.path.lexists(path):
        if not stat.S_ISSOCK(os.lstat(path).st_mode):
            log.warning("%s exists and is not a socket; leaving it alone", path)
            return Acquired(None, False)
        try:
            if paths is None:
                with socket.socket(socket.AF_UNIX) as probe:
                    probe.settimeout(ack_timeout)
                    probe.connect(str(path))
                return Acquired(None, False)  # a live instance: no second listener
            return Acquired(None, _send(path, paths, ack_timeout))
        except (ConnectionRefusedError, FileNotFoundError):
            os.unlink(path)  # nobody listens: a crashed instance's leftover
        except OSError as e:  # timeout, reset, ...: it exists but did not answer
            log.warning("running instance not reachable: %s", e)
            return Acquired(None, False)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        sock.bind(str(path))
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    sock.listen(8)
    return Acquired(Listener(sock, path), False)
