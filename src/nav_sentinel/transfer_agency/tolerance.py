"""Detecting register breaks. Arithmetic over two books, no model.

The same division the fund-accounting spine uses: deciding whether two unit counts differ is
subtraction, and subtraction should not be delegated to a language model.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

from nav_sentinel.transfer_agency import register
from nav_sentinel.transfer_agency.models import (
    RegisterBreak,
    RegisterBreakType,
    RegisterCase,
)

#: A ten-thousandth of a unit. Registers deal in fractional units, so exact equality is the
#: wrong test. (This comment said "a quarter of a unit", which is 2,500 times the value.)
TOLERANCE = Decimal("0.0001")


def _break_id(*parts: object) -> str:
    """Content-hashed, not counted -- the same reason case ids are."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12]
    return f"TABRK-{digest}"


def detect(fund_id: str, as_of: date) -> list[RegisterCase]:
    """Compare the registrar's balances against the fund's unit ledger, per holder."""
    registrar = {p.holder_id: p.units for p in register.positions("registrar", fund_id)
                 if p.as_of == as_of}
    ledger = {p.holder_id: p.units for p in register.positions("fund_accounting", fund_id)
              if p.as_of == as_of}

    cases: list[RegisterCase] = []
    for holder_id in sorted(registrar | ledger):
        theirs = registrar.get(holder_id, Decimal(0))
        ours = ledger.get(holder_id, Decimal(0))
        if abs(theirs - ours) <= TOLERANCE:
            continue
        item = RegisterBreak(
            break_id=_break_id(fund_id, as_of, holder_id, theirs, ours),
            fund_id=fund_id,
            as_of=as_of,
            break_type=RegisterBreakType.HOLDER_BALANCE,
            holder_id=holder_id,
            registrar_units=theirs,
            ledger_units=ours,
            tolerance_applied=TOLERANCE,
        )
        cases.append(
            RegisterCase(
                # Derived from what the case is, like the fund-accounting side, so a re-run is
                # byte-identical.
                case_id=f"TACASE-{fund_id}-{as_of.isoformat()}-holder-{holder_id}",
                fund_id=fund_id,
                as_of=as_of,
                breaks=[item],
                units_at_risk=abs(item.difference),
            )
        )
    return cases


def control_total(fund_id: str, as_of: date) -> Decimal:
    """Total units the two books disagree about. This process's control total, in units."""
    # Every break, not the first of each case: `detect` builds one break per case today, and a
    # control total that silently ignored the rest would understate the very number it exists to
    # state.
    return sum(
        (abs(b.difference) for c in detect(fund_id, as_of) for b in c.breaks), Decimal(0)
    )
