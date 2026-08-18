"""Read-only access to the fund's books and records.

This is the only tool that touches internal data, and it is deliberately read-only:
nothing in the fleet has a write path to golden records. Investigators receive a
capability-scoped subset of these functions via the Agent Registry manifest.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

from nav_sentinel.domain.models import (
    CashMovement,
    Fund,
    NavRecord,
    Position,
    Security,
    Trade,
)

DATA = Path(__file__).resolve().parents[3] / "fixtures" / "data"
SOURCE_NAME = "books_and_records"


@lru_cache(maxsize=16)
def _load(name: str) -> tuple[dict, ...]:
    path = DATA / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing -- run `python fixtures/generate.py` first (see README > Spin-up)."
        )
    return tuple(json.loads(path.read_text()))


def funds() -> list[Fund]:
    return [Fund(**f) for f in _load("funds")]


def securities() -> list[Security]:
    return [Security(**s) for s in _load("securities")]


def security(isin: str) -> Security | None:
    return next((s for s in securities() if s.isin == isin), None)


def positions(source: str, fund_id: str | None = None) -> list[Position]:
    rows = _load(f"positions_{source}")
    out = [Position(**r) for r in rows]
    return [p for p in out if fund_id is None or p.fund_id == fund_id]


def cash_movements(source: str, fund_id: str | None = None) -> list[CashMovement]:
    rows = _load(f"cash_{source}")
    out = [CashMovement(**r) for r in rows]
    return [c for c in out if fund_id is None or c.fund_id == fund_id]


def nav_records(source: str, fund_id: str | None = None) -> list[NavRecord]:
    rows = _load(f"nav_{source}")
    out = [NavRecord(**r) for r in rows]
    return [n for n in out if fund_id is None or n.fund_id == fund_id]


def nav_record(source: str, fund_id: str, as_of: date) -> NavRecord | None:
    return next(
        (n for n in nav_records(source, fund_id) if n.as_of == as_of),
        None,
    )


def trades(fund_id: str | None = None) -> list[Trade]:
    out = [Trade(**t) for t in _load("trades")]
    return [t for t in out if fund_id is None or t.fund_id == fund_id]


def trades_for_security(fund_id: str, isin: str) -> list[Trade]:
    return [t for t in trades(fund_id) if t.isin == isin]
