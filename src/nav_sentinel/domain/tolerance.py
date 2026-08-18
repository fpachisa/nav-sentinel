"""Deterministic break detection.

No model is involved here, on purpose. Deciding *whether* two numbers disagree is
arithmetic; spending a Gemini call on it would be slower, costlier and less auditable.
The fleet's models are reserved for explaining *why* they disagree.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import count

from nav_sentinel.domain.models import (
    BreakType,
    CashMovement,
    NavRecord,
    Position,
    ReconciliationBreak,
)

# Tolerances are policy, and live next to the materiality thresholds in config-like form
# so that an auditor can read them without reading Python.
QUANTITY_TOLERANCE = Decimal("0.0001")
MARKET_VALUE_TOLERANCE_BASE = Decimal("1.00")
CASH_TOLERANCE_BASE = Decimal("1.00")
NAV_PER_SHARE_TOLERANCE = Decimal("0.0001")

_counter = count(1)


def _next_id(as_of: str) -> str:
    return f"BRK-{as_of}-{next(_counter):05d}"


def _key(p: Position) -> tuple[str, str]:
    return (p.fund_id, p.isin)


class _Aggregate:
    """Summed holding for one (fund, security).

    Funds routinely hold the same security across several tax lots or as a settled
    holding plus an in-flight trade, so a position extract legitimately contains more
    than one row per security. Aggregating is mandatory: keying rows into a dict would
    silently discard lots and under-report the break.
    """

    __slots__ = ("as_of", "local_currency", "market_value_base", "quantity", "rows")

    def __init__(self, p: Position) -> None:
        self.quantity = Decimal(0)
        self.market_value_base = Decimal(0)
        self.local_currency = p.local_currency
        self.as_of = p.as_of
        self.rows = 0
        self.add(p)

    def add(self, p: Position) -> None:
        self.quantity += p.quantity
        self.market_value_base += p.market_value_base
        self.as_of = max(self.as_of, p.as_of)
        self.rows += 1


def _aggregate(positions: list[Position]) -> dict[tuple[str, str], _Aggregate]:
    out: dict[tuple[str, str], _Aggregate] = {}
    for p in positions:
        k = _key(p)
        if k in out:
            out[k].add(p)
        else:
            out[k] = _Aggregate(p)
    return out


def detect_position_breaks(
    accounting: list[Position], custodian: list[Position]
) -> list[ReconciliationBreak]:
    """Compare position quantity and base-currency market value, side by side."""
    acc = _aggregate(accounting)
    cus = _aggregate(custodian)
    breaks: list[ReconciliationBreak] = []

    for k in sorted(acc.keys() | cus.keys()):
        a, c = acc.get(k), cus.get(k)
        fund_id, isin = k
        as_of = (a or c).as_of  # type: ignore[union-attr]
        stamp = as_of.isoformat()

        a_qty = a.quantity if a else Decimal(0)
        c_qty = c.quantity if c else Decimal(0)
        if abs(a_qty - c_qty) > QUANTITY_TOLERANCE:
            breaks.append(
                ReconciliationBreak(
                    break_id=_next_id(stamp),
                    fund_id=fund_id,
                    as_of=as_of,
                    break_type=BreakType.POSITION_QUANTITY,
                    isin=isin,
                    accounting_value=a_qty,
                    custodian_value=c_qty,
                    tolerance_applied=QUANTITY_TOLERANCE,
                )
            )

        a_mv = a.market_value_base if a else Decimal(0)
        c_mv = c.market_value_base if c else Decimal(0)
        if abs(a_mv - c_mv) > MARKET_VALUE_TOLERANCE_BASE:
            breaks.append(
                ReconciliationBreak(
                    break_id=_next_id(stamp),
                    fund_id=fund_id,
                    as_of=as_of,
                    break_type=BreakType.MARKET_VALUE,
                    isin=isin,
                    currency=(a or c).local_currency,  # type: ignore[union-attr]
                    accounting_value=a_mv,
                    custodian_value=c_mv,
                    tolerance_applied=MARKET_VALUE_TOLERANCE_BASE,
                )
            )

    return breaks


def detect_cash_breaks(
    accounting: list[CashMovement], custodian: list[CashMovement]
) -> list[ReconciliationBreak]:
    """Compare cash balances per currency. A missing dividend on one side shows up here."""

    def totals(movements: list[CashMovement]) -> dict[tuple[str, str], Decimal]:
        out: dict[tuple[str, str], Decimal] = {}
        for m in movements:
            key = (m.fund_id, m.currency)
            out[key] = out.get(key, Decimal(0)) + m.amount
        return out

    acc, cus = totals(accounting), totals(custodian)
    all_movements = accounting + custodian
    as_of = max(m.value_date for m in all_movements) if all_movements else None
    if as_of is None:
        return []

    breaks: list[ReconciliationBreak] = []
    for key in sorted(acc.keys() | cus.keys()):
        fund_id, ccy = key
        a, c = acc.get(key, Decimal(0)), cus.get(key, Decimal(0))
        if abs(a - c) > CASH_TOLERANCE_BASE:
            breaks.append(
                ReconciliationBreak(
                    break_id=_next_id(as_of.isoformat()),
                    fund_id=fund_id,
                    as_of=as_of,
                    break_type=BreakType.CASH_BALANCE,
                    currency=ccy,
                    accounting_value=a,
                    custodian_value=c,
                    tolerance_applied=CASH_TOLERANCE_BASE,
                )
            )
    return breaks


def detect_nav_breaks(
    accounting: list[NavRecord], custodian: list[NavRecord]
) -> list[ReconciliationBreak]:
    acc = {(n.fund_id, n.as_of): n for n in accounting}
    cus = {(n.fund_id, n.as_of): n for n in custodian}
    breaks: list[ReconciliationBreak] = []
    for key in sorted(acc.keys() & cus.keys()):
        fund_id, as_of = key
        a, c = acc[key], cus[key]
        if abs(a.nav_per_share - c.nav_per_share) > NAV_PER_SHARE_TOLERANCE:
            breaks.append(
                ReconciliationBreak(
                    break_id=_next_id(as_of.isoformat()),
                    fund_id=fund_id,
                    as_of=as_of,
                    break_type=BreakType.NAV_PER_SHARE,
                    accounting_value=a.nav_per_share,
                    custodian_value=c.nav_per_share,
                    tolerance_applied=NAV_PER_SHARE_TOLERANCE,
                )
            )
    return breaks
