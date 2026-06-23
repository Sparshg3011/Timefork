"""Timeline diff: line up two runs and show their shared prefix and the first
point where they diverge -- the heart of time-travel debugging.
"""

from dataclasses import dataclass

import psycopg

from .events import Event, read_events


@dataclass(frozen=True)
class DiffRow:
    seq: int
    a: str | None  # short description of run A's event at this seq (None if absent)
    b: str | None
    same: bool


def _describe(e: Event | None) -> str | None:
    if e is None:
        return None
    if e.type == "LLM_CALLED":
        return f"LLM_CALLED {e.payload.get('prompt', '')}"
    if e.type == "PATCH_APPLIED":
        return f"PATCH_APPLIED {e.payload}"
    if e.type in ("TOOL_INTENT", "TOOL_COMPLETED"):
        return f"{e.type} {e.payload.get('key', '')}"
    if e.type == "APPROVAL_REQUESTED":
        return f"APPROVAL_REQUESTED {e.payload.get('question', '')}"
    if e.type == "APPROVAL":
        return f"APPROVAL {'yes' if e.payload.get('approved') else 'no'}"
    return e.type


def _same(a: Event | None, b: Event | None) -> bool:
    return a is not None and b is not None and a.type == b.type and a.payload == b.payload


def diff_runs(conn: psycopg.Connection, run_a: str, run_b: str) -> dict:
    """Compare two runs event-by-event. Returns the shared-prefix length, the seq
    of the first divergence (None if one is a prefix of the other), and a row per
    seq for display."""
    a = read_events(conn, run_a)
    b = read_events(conn, run_b)
    rows = []
    shared = 0
    diverge_at = None
    for i in range(max(len(a), len(b))):
        ea = a[i] if i < len(a) else None
        eb = b[i] if i < len(b) else None
        same = _same(ea, eb)
        if diverge_at is None:
            if same:
                shared += 1
            else:
                diverge_at = i + 1
        rows.append(DiffRow(i + 1, _describe(ea), _describe(eb), same))
    return {"shared": shared, "diverge_at": diverge_at, "rows": rows}
