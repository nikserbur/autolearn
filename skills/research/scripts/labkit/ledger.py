"""The research ledger: an append-only, hash-chained JSON-lines file holding the protocol's state
(registrations, runs, judgements, holdout looks), and the machine-readable pass criteria it judges.
Standard library only, Python 3.9+.

Each line carries `prev`, the sha256 of the line before it, so an edit or a deleted line anywhere
breaks the chain and `lab.py check` says where.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import operator
import os
import pathlib
import re

LEDGER = "LEDGER.jsonl"
CLAUSE = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*(>=|<=|==|!=|>|<)\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*$")
OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq, "!=": operator.ne}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    return sha256_bytes(path.read_bytes()) if path.is_file() else ""


class Ledger:
    def __init__(self, lab: pathlib.Path):
        self.lab = lab
        self.path = lab / LEDGER

    def lines(self) -> list:
        if not self.path.exists():
            return []
        return [line for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def entries(self) -> list:
        return [json.loads(line) for line in self.lines()]

    def append(self, kind: str, **fields) -> dict:
        lines = self.lines()
        entry = {"seq": len(lines) + 1, "ts": datetime.datetime.now().isoformat(timespec="seconds"), "kind": kind,
                 **fields, "prev": sha256_bytes(lines[-1].encode("utf-8")) if lines else ""}
        self.lab.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return entry

    def broken(self) -> list:
        """Where the chain does not hold: an edited, inserted or deleted line."""
        problems, prev = [], ""
        for n, line in enumerate(self.lines(), 1):
            try:
                entry = json.loads(line)
            except ValueError:
                problems.append(f"line {n} is not JSON")
                prev = sha256_bytes(line.encode("utf-8"))
                continue
            if entry.get("prev") != prev or entry.get("seq") != n:
                problems.append(f"line {n} ({entry.get('kind')} {entry.get('id', '')}) does not follow line {n - 1}")
            prev = sha256_bytes(line.encode("utf-8"))
        return problems

    def find(self, kind: str, id: str = None) -> list:
        return [e for e in self.entries() if e.get("kind") == kind and (id is None or e.get("id") == id)]

    def last(self, kind: str, id: str = None):
        found = self.find(kind, id)
        return found[-1] if found else None


def parse_criterion(text: str) -> list:
    """'net_per_trade > 0 & trades >= 300' -> [(metric, op, number), ...]. Raises ValueError."""
    clauses = []
    for part in (text or "").split("&"):
        m = CLAUSE.match(part)
        if not m:
            raise ValueError(f"cannot read '{part.strip()}': write <metric> <op> <number>, joined by &")
        clauses.append((m.group(1), m.group(2), float(m.group(3))))
    return clauses


def judge(clauses: list, metrics: dict):
    """(verdict, lines) for the criterion on these metrics. A missing metric raises KeyError."""
    lines, passed = [], True
    for name, op, bound in clauses:
        if name not in metrics:
            raise KeyError(name)
        ok = OPS[op](metrics[name], bound)
        passed &= ok
        lines.append(f"{name} {metrics[name]:g} {op} {bound:g} {'ok' if ok else 'NO'}")
    return ("PASS" if passed else "FAIL"), lines


def _number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def read_metrics(path: pathlib.Path) -> dict:
    """Numbers from a metrics file: a TSV header with one row, or `name<TAB>value` lines."""
    rows = [line.split("\t") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    header = len(rows) >= 2 and len(rows[0]) == len(rows[1]) and not any(map(_number, rows[0]))
    pairs = zip(rows[0], rows[1]) if header else ((r[0], r[1]) for r in rows if len(r) >= 2)
    out = {}
    for name, value in pairs:
        try:
            out[name.strip()] = float(value)
        except ValueError:
            continue
    return out


def read_journal(path: pathlib.Path) -> dict:
    """EXPERIMENTS.tsv rows by id."""
    if not path.exists():
        return {}
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    head = lines[0].split("\t") if lines else []
    rows = (dict(zip(head, line.split("\t"))) for line in lines[1:])
    return {r.get("id", ""): r for r in rows if r.get("id")}
