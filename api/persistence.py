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

Code-quality-tracker item C1 (atomic writes).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


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
