"""Markdown tables in the research KB: the requirement tables of ENVIRONMENT.md and the stage table
of an expect file. Standard library only."""
from __future__ import annotations

import pathlib
import re

PROVEN = {"✅", "ok", "yes", "pass", "done"}
NOT_APPLICABLE = {"n/a", "na", "-"}
STAGE_PREFIX = {"sim": "S", "verify": "V"}


def table_rows(text: str, needs: tuple) -> list:
    """Data rows of the markdown tables whose header has all the `needs` columns, as dicts."""
    rows, head = [], None
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            head = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if head is None:
            lowered = [c.lower() for c in cells]
            head = lowered if all(n in lowered for n in needs) else []
        elif head and not all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            rows.append(dict(zip(head, cells)))
    return rows


def requirements(path: pathlib.Path, stage: str = "all") -> list:
    rows = table_rows(path.read_text(encoding="utf-8"), ("requirement", "status")) if path.is_file() else []
    if stage != "all":
        rows = [r for r in rows if r.get("#", "").upper().startswith(STAGE_PREFIX[stage])]
    return rows


def unproven(rows: list) -> list:
    """One line for each requirement that is not shown to hold."""
    out = []
    for r in rows:
        status, evidence = r.get("status", "").lower(), r.get("evidence", "")
        name = f"{r.get('#', '')} {r.get('requirement', '')[:70]}".strip()
        if status in PROVEN and not evidence:
            out.append(f"{name}: marked done without evidence")
        elif status in NOT_APPLICABLE and not evidence:
            out.append(f"{name}: n/a without a reason")
        elif status not in PROVEN | NOT_APPLICABLE:
            out.append(f"{name}: {'not met' if status in ('❌', 'no', 'fail') else 'not checked'}")
    return out
