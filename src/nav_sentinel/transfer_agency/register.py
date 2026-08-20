"""Reading the share register. This process's equivalent of books and records.

Committed fixtures, no network, for the same reason the fund-accounting side uses them: `make eval`
and the test suite have to run with nothing reachable.
"""

from __future__ import annotations

import json
from datetime import date
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
