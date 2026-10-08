"""requirements-lock.txt must satisfy requirements.txt.

Every workflow installs from the lock, so a pin bumped in requirements.txt alone
would never reach CI. This catches that drift without a network call.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _version(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", v))


def _lines(path: Path) -> list[str]:
    return [l.split("#")[0].strip() for l in path.read_text().splitlines() if l.split("#")[0].strip()]


def _locked() -> dict[str, str]:
    return {_norm(n): v for n, v in (l.split("==") for l in _lines(ROOT / "requirements-lock.txt"))}


def test_lock_is_exact_pins_only() -> None:
    for line in _lines(ROOT / "requirements-lock.txt"):
        assert re.fullmatch(r"[A-Za-z0-9_.\-]+==[A-Za-z0-9_.]+", line), line


def test_every_requirement_is_locked_within_its_specifier() -> None:
    locked = _locked()
    ops = {
        "==": lambda a, b: a == b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b,
        "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
    }
    for line in _lines(ROOT / "requirements.txt"):
        name, spec = re.match(r"([A-Za-z0-9_.\-]+)\s*(.*)", line).groups()
        assert _norm(name) in locked, f"{name} is missing from requirements-lock.txt"
        have = _version(locked[_norm(name)])
        for clause in filter(None, (c.strip() for c in spec.split(","))):
            op, want = re.match(r"(==|>=|<=|<|>)\s*(.+)", clause).groups()
            assert ops[op](have, _version(want)), f"{name}=={locked[_norm(name)]} fails {clause}"
