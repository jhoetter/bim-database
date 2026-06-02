"""C1 (code-quality-tracker): atomic-write persistence helpers.

Guards the crash-corruption property: a write that is interrupted must
leave the *previous* file intact and valid, never a truncated/empty file,
and must not strand a partial temp file.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from api import persistence
from api.persistence import atomic_write_json, atomic_write_text, locked_path


def test_atomic_write_json_roundtrip(tmp_path):
    p = tmp_path / "doc.json"
    obj = {"b": 2, "a": 1, "nested": {"x": [1, 2, 3]}}
    atomic_write_json(p, obj)
    assert json.loads(p.read_text()) == obj


def test_atomic_write_json_matches_legacy_default_format(tmp_path):
    """Defaults must reproduce the old `json.dumps(..., indent=2,
    ensure_ascii=False)` output byte-for-byte so on-disk files are stable."""
    p = tmp_path / "doc.json"
    obj = {"umlaut": "Größe", "n": 1}
    atomic_write_json(p, obj)
    assert p.read_text() == json.dumps(obj, indent=2, ensure_ascii=False)


def test_atomic_write_json_trailing_newline_and_sort(tmp_path):
    p = tmp_path / "state.json"
    obj = {"b": 2, "a": 1}
    atomic_write_json(p, obj, sort_keys=True, trailing_newline=True)
    text = p.read_text()
    assert text.endswith("\n")
    assert text == json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def test_atomic_write_creates_parent_dirs(tmp_path):
    p = tmp_path / "a" / "b" / "c" / "doc.json"
    atomic_write_json(p, {"ok": True})
    assert json.loads(p.read_text()) == {"ok": True}


def test_interrupted_write_leaves_original_intact(tmp_path, monkeypatch):
    """If os.replace fails mid-write, the previous file content survives
    and stays valid JSON — the corruption scenario C1 fixes."""
    p = tmp_path / "doc.json"
    atomic_write_json(p, {"version": 1})
    original = p.read_text()

    def boom(*_a, **_k):
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(persistence.os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_json(p, {"version": 2})

    # Original is untouched and still valid.
    assert p.read_text() == original
    assert json.loads(p.read_text()) == {"version": 1}


def test_interrupted_write_strands_no_temp_file(tmp_path, monkeypatch):
    p = tmp_path / "doc.json"
    atomic_write_json(p, {"version": 1})

    monkeypatch.setattr(
        persistence.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("x"))
    )
    with pytest.raises(OSError):
        atomic_write_json(p, {"version": 2})

    leftovers = [q.name for q in tmp_path.iterdir() if q.name != "doc.json"]
    assert leftovers == [], f"temp file(s) left behind: {leftovers}"


def test_interrupted_write_to_new_path_creates_nothing(tmp_path, monkeypatch):
    """A failed first write to a not-yet-existing target leaves no file."""
    p = tmp_path / "fresh.json"
    monkeypatch.setattr(
        persistence.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("x"))
    )
    with pytest.raises(OSError):
        atomic_write_json(p, {"version": 1})
    assert not p.exists()
    assert list(tmp_path.iterdir()) == []


def test_atomic_write_text_roundtrip(tmp_path):
    p = tmp_path / "plan.md"
    atomic_write_text(p, "# Plan\n\n- step 1\n")
    assert p.read_text() == "# Plan\n\n- step 1\n"


# ── C2: locking ─────────────────────────────────────────────────────────────


def test_locked_path_prevents_lost_updates(tmp_path):
    """N threads each read-modify-write the same file under locked_path.
    With serialization every update survives; without the lock the
    sleep-widened read-modify-write window would drop most of them."""
    p = tmp_path / "doc.json"
    atomic_write_json(p, {"items": []})
    n = 30

    def worker(i: int) -> None:
        with locked_path(p):
            doc = json.loads(p.read_text())
            doc["items"].append(i)
            time.sleep(0.001)  # widen the read→write window
            atomic_write_json(p, doc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = json.loads(p.read_text())
    assert sorted(final["items"]) == list(range(n))


def test_locked_path_is_mutually_exclusive(tmp_path):
    """The second acquirer must wait until the first releases."""
    p = tmp_path / "doc.json"
    order: list[str] = []

    def first() -> None:
        with locked_path(p):
            order.append("first-enter")
            time.sleep(0.05)
            order.append("first-exit")

    def second() -> None:
        time.sleep(0.01)  # ensure `first` grabs the lock first
        with locked_path(p):
            order.append("second-enter")

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert order == ["first-enter", "first-exit", "second-enter"]


def test_locked_path_releases_for_reacquire(tmp_path):
    """Sequential acquire/release of the same path must not block."""
    p = tmp_path / "doc.json"
    with locked_path(p):
        pass
    with locked_path(p):  # would hang if the lock leaked
        atomic_write_json(p, {"ok": True})
    assert json.loads(p.read_text()) == {"ok": True}


def test_locked_path_distinct_paths_do_not_block(tmp_path):
    """Different files use different locks — no false contention."""
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    with locked_path(a):
        with locked_path(b):  # distinct path → must not deadlock
            atomic_write_json(b, {"b": 1})
        atomic_write_json(a, {"a": 1})
    assert json.loads(a.read_text()) == {"a": 1}
    assert json.loads(b.read_text()) == {"b": 1}
