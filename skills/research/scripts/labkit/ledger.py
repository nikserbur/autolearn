"""The research ledger: an append-only, hash-chained JSON-lines file holding the protocol's state
(registrations, runs, judgements, holdout looks), and the machine-readable pass criteria it judges.
Standard library only, Python 3.9+.

Each line carries `prev`, the sha256 of the line before it, and `LEDGER.head` holds the number and
hash of the last line, so an edited, inserted or deleted line anywhere, the last one included,
shows in `lab.py check`. Appends take an exclusive lock, so runs in the background can finish at
the same time.
"""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import math
import operator
import os
import pathlib
import re

LEDGER, HEAD = "LEDGER.jsonl", "LEDGER.head"
ID = re.compile(r"^[A-Za-z0-9][\w.-]{0,63}$")
CLAUSE = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*(>=|<=|==|!=|>|<)\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*$")
OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq, "!=": operator.ne}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    return sha256_bytes(path.read_bytes()) if path.is_file() else ""


def text_lines(path: pathlib.Path) -> list:
    """Lines split on newlines only: str.splitlines() would also split on U+2028 and form feeds."""
    if not path.exists():
        return []
    return [line.rstrip("\r") for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


@contextlib.contextmanager
def locked(path: pathlib.Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as f:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class Ledger:
    def __init__(self, lab: pathlib.Path):
        self.lab = lab
        self.path = lab / LEDGER
        self.head = lab / HEAD

    def lines(self) -> list:
        return text_lines(self.path)

    def entries(self) -> list:
        """Readable entries; unreadable lines are skipped here and reported by broken()."""
        out = []
        for line in self.lines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                out.append(entry)
        return out

    def append(self, kind: str, compute=None, **fields) -> dict:
        """Append one entry. `compute(ledger)` adds fields read from the ledger under the same lock,
        so what it counts cannot change before the entry is written."""
        with locked(self.lab / ".ledger.lock"):
            if compute:
                fields = {**fields, **compute(self)}
            lines = self.lines()
            entry = {"seq": len(lines) + 1, "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                     "kind": kind, **fields, "prev": sha256_bytes(lines[-1].encode("utf-8")) if lines else ""}
            line = json.dumps(entry, ensure_ascii=True, sort_keys=True, allow_nan=False)
            with self.path.open("a", encoding="utf-8") as f:
                if self.path.stat().st_size and not self.path.read_bytes().endswith(b"\n"):
                    f.write("\n")
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
            self.head.write_text(f"{entry['seq']} {sha256_bytes(line.encode('utf-8'))}\n", encoding="utf-8")
        return entry

    def broken(self) -> list:
        """Where the chain does not hold: an edited, inserted or deleted line, the last one included."""
        problems, prev, lines = [], "", self.lines()
        for n, line in enumerate(lines, 1):
            try:
                entry = json.loads(line)
            except ValueError:
                entry = None
            if not isinstance(entry, dict):
                problems.append(f"line {n} is not a ledger entry")
            elif entry.get("prev") != prev or entry.get("seq") != n:
                problems.append(f"line {n} ({entry.get('kind')} {entry.get('id', '')}) does not follow line {n - 1}")
            prev = sha256_bytes(line.encode("utf-8"))
        if self.head.exists():
            seq, _, digest = self.head.read_text(encoding="utf-8").strip().partition(" ")
            if not seq.isdigit() or int(seq) != len(lines) or (lines and digest != prev):
                problems.append(f"the last line is not the one {HEAD} recorded (entry {seq}): "
                                "the end of the ledger was edited or removed")
        elif lines:
            problems.append(f"{HEAD} is missing")
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


def _number(text: str):
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def read_metrics(path: pathlib.Path) -> dict:
    """Finite numbers from a metrics file: either `name<TAB>value` lines (a non-numeric header such as
    `metric<TAB>value` is skipped), or a header row with exactly one row of values. Several value rows
    (one per seed, say) raise ValueError: the criterion needs one row of totals."""
    rows = [line.rstrip("\t").split("\t") for line in text_lines(path)]
    if not rows:
        return {}
    data = rows[1:] if _number(rows[0][-1]) is None and len(rows) > 1 else rows
    if all(len(r) == 2 and _number(r[0]) is None for r in data):
        pairs = ((r[0], r[1]) for r in data)
    elif len(rows) == 2:
        pairs = zip(rows[0], rows[1])
    else:
        raise ValueError(f"{len(rows) - 1} rows of values: write one row of totals (aggregate seeds and days first)")
    out = {}
    for name, value in pairs:
        number = _number(value)
        if number is not None:
            out[name.strip()] = number
    return out


def read_journal(path: pathlib.Path) -> dict:
    """EXPERIMENTS.tsv rows by id."""
    lines = text_lines(path)
    head = lines[0].split("\t") if lines else []
    rows = (dict(zip(head, line.split("\t"))) for line in lines[1:])
    return {r.get("id", ""): r for r in rows if r.get("id")}
