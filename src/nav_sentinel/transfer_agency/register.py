"""Reading the share register. This process's equivalent of books and records.

Committed fixtures, no network, for the same reason the fund-accounting side uses them: `make eval`
and the test suite have to run with nothing reachable.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from nav_sentinel.transfer_agency.models import Deal, HolderPosition

REGISTER = Path(__file__).resolve().parents[3] / "fixtures" / "data" / "ta_register.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(REGISTER.read_text())


def positions(source: str, fund_id: str | None = None) -> list[HolderPosition]:
    """Unit-holder balances from one book: `registrar` or `fund_accounting`."""
    return [
        HolderPosition.model_validate(row)
        for row in _data()["positions"]
        if row["source"] == source and (fund_id is None or row["fund_id"] == fund_id)
    ]


def deals(fund_id: str | None = None) -> list[Deal]:
    """Instructions on the register, whether settled or not."""
    return [
        Deal.model_validate(row)
        for row in _data()["deals"]
        if fund_id is None or row["fund_id"] == fund_id
    ]


def dealt_on(fund_id: str, trade_date: date) -> dict[str, object]:
    """How many holders dealt on one dealing date, and for how many units. **Aggregated here.**

    The transfer-agency answer to a question fund accounting cannot ask of its own books: *who dealt
    at the price we published, and for how much.* The register knows because it records a trade date
    per deal; the fund ledger does not, because it recognises deals on settlement.

    Returns counts, not deals, and the aggregation is the control rather than a convenience. The
    first version returned the matching `Deal` objects, so every `holder_id` reached the model and
    the audit record -- while the reporting agent's prompt asked it not to list investor identities.
    An instruction is not a control: the model was shown every identity and told to be discreet.
    A materiality assessment turns on *how many* investors were affected, so the identities are data
    no decision here consumes, and the way to not leak them is to not fetch them.

    The register investigator still reads holder-level rows through its own tools, because
    explaining one holder's balance genuinely requires that holder. Different capability, different
    data scope -- which is the point of scoping by capability rather than by department.
    """
    matched = [deal for deal in deals(fund_id) if deal.trade_date == trade_date]
    return {
        "trade_date": trade_date.isoformat(),
        "holders": len({deal.holder_id for deal in matched}),
        "units": sum((deal.units for deal in matched), Decimal(0)),
        "deals": len(matched),
    }


def in_transit(fund_id: str, as_of: date) -> list[Deal]:
    """Deals dealt on or before the valuation point but settling after it.

    The whole transfer-agency scenario in one function, and it is arithmetic. The registrar counts a
    subscription from its trade date; the fund's unit ledger recognises it on settlement. Between
    the two dates the books disagree by exactly those units, and neither is wrong.
    """
    return [
        deal
        for deal in deals(fund_id)
        if deal.trade_date <= as_of < deal.settlement_date
    ]
