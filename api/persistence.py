"""Crash-safe persistence helpers.

Every JSON record and Markdown plan in this service is a plain file on
disk (`data/dataset/<key>/...`). The naive `path.write_text(json.dumps(...))`
pattern truncates the target to zero length and *then* streams the new
bytes — if the process dies in between (this box hard-crashes from CPU
MCEs), the file is left truncated/empty, i.e. invalid JSON, and the next
read either raises or is silently re-read as "no labels".

These helpers make every write **atomic**: the new content is written to a
temp file in the same directory, fsync'd, then `os.replace()`'d over the
target. `os.replace` is atomic on POSIX when source and destination are on
the same filesystem, so a reader ever only sees the complete old file or
the complete new file — never a half-written one. On any failure the temp
file is removed and the original is left untouched.

Code-quality-tracker items C1 (atomic writes) and C2 (per-file locking via
`locked_path`).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def atomic_write_text(path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace ``path`` with ``text``.

    Writes to a temp file in the same directory, flushes + fsyncs it, then
    `os.replace()`s it over the destination. Creates parent dirs as needed.
    The original file is untouched if anything fails.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Leave the original intact; never strand a partial temp file.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def atomic_write_json(
    path: Path | str,
    obj: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    sort_keys: bool = False,
    trailing_newline: bool = False,
) -> None:
    """Serialize ``obj`` to JSON and write it atomically.

    Defaults mirror the previous in-line `json.dumps(..., indent=2,
    ensure_ascii=False)` calls so on-disk output is byte-identical except
    where a caller opts into ``sort_keys`` / ``trailing_newline``.
    """
    text = json.dumps(obj, indent=indent, ensure_ascii=ensure_ascii, sort_keys=sort_keys)
    if trailing_newline:
        text += "\n"
    atomic_write_text(path, text)


# ── C2: per-file locking ───────────────────────────────────────────────────
# FastAPI runs sync `def` handlers in a threadpool, so two requests touching
# the same scene genuinely execute in parallel and can interleave a
# read-modify-write (lost update). Each file is guarded by a process-local
# mutex keyed by its absolute path, plus an advisory OS-level `fcntl.flock`
# on a sidecar `.lock` file so a *second process* (a CLI, a test runner,
# another worker) serializes against the API too. Hold the lock across the
# whole read → mutate → write, not just the write itself.

_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


@contextmanager
def locked_path(path: Path | str) -> Iterator[Path]:
    """Serialize read-modify-write access to ``path``.

    Acquires a process-local mutex for the path *and* an advisory OS lock on
    a sidecar ``<name>.lock`` file. Use around any read → mutate →
    :func:`atomic_write_json` sequence::

        with locked_path(p):
            doc = json.loads(p.read_text()) if p.exists() else {}
            doc["x"] = 1
            atomic_write_json(p, doc)

    The sidecar ``.lock`` file is created next to the target and left on disk
    (cheap, and avoids a delete/recreate race). Not re-entrant — do not nest
    ``locked_path`` on the same path within one thread.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_for(path)
    lock.acquire()
    fcntl = None
    fh = None
    try:
        try:
            import fcntl as _fcntl  # POSIX only

            fcntl = _fcntl
            fh = open(path.with_name(path.name + ".lock"), "w")
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            # No fcntl (non-POSIX) or lock file unopenable: fall back to the
            # in-process mutex alone, which still fixes the threadpool race.
            fh = None
        yield path
    finally:
        if fh is not None and fcntl is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            fh.close()
        lock.release()
