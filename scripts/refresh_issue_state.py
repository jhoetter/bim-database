#!/usr/bin/env python3
"""Refresh the cached state of every bim_ai_blocking_issues reference into
data/.issue_state.json. The API reads this cache to derive each house's
`modelable_in_bim_ai` boolean. Run via `make refresh-issue-state`.

Uses the `gh` CLI to query GitHub — no extra Python deps needed."""
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent
HOUSES = BASE / "data" / "houses"
CACHE = BASE / "data" / ".issue_state.json"


def collect_refs() -> set[str]:
    refs: set[str] = set()
    for p in HOUSES.glob("house-*.json"):
        try:
            rec = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        for r in rec.get("bim_ai_blocking_issues") or []:
            refs.add(r)
    return refs


def fetch_state(ref: str) -> str:
    """Returns 'open' | 'closed' | 'unknown'."""
    try:
        repo, _, num = ref.partition("#")
        r = subprocess.run(
            ["gh", "issue", "view", num, "--repo", repo, "--json", "state"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        state = json.loads(r.stdout)["state"].lower()
        # gh returns 'OPEN' or 'CLOSED'
        return "closed" if state == "closed" else "open"
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"warn: gh failed for {ref}: {e.stderr.strip()}\n")
        return "unknown"
    except (KeyError, json.JSONDecodeError, subprocess.TimeoutExpired) as e:
        sys.stderr.write(f"warn: parse failed for {ref}: {e}\n")
        return "unknown"


def main():
    refs = sorted(collect_refs())
    if not refs:
        CACHE.write_text("{}\n")
        print("no blocking-issue refs found; wrote empty cache")
        return
    state = {ref: fetch_state(ref) for ref in refs}
    CACHE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    n_open = sum(1 for v in state.values() if v == "open")
    n_closed = sum(1 for v in state.values() if v == "closed")
    n_unk = sum(1 for v in state.values() if v == "unknown")
    print(f"refreshed {len(state)} issue(s) — open={n_open} closed={n_closed} unknown={n_unk}")


if __name__ == "__main__":
    main()
