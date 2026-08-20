"""Correcting a register break. No model, and that is the claim rather than a shortcut.

A subscription in transit is corrected by adding the in-transit units to the fund's ledger. The
registrar recognises the deal from its trade date, the ledger from its settlement date, and the
difference between the two books is exactly the dealt units. There is no judgement in that -- it is
subtraction against a date.

So this process does not put a language model on the step. A fleet that uses judgement where
judgement is required and deterministic logic where it is not is a better claim than one that puts
an LLM on every step, and the honest way to make that claim is to have a process that demonstrably
does the second thing.

The governance path is unchanged: the correction is still a *proposal*, it is still banded by the
control plane from a unit-tagged magnitude, and nothing here posts anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from nav_sentinel.transfer_agency import register
from nav_sentinel.transfer_agency.models import RegisterCase


@dataclass(frozen=True)
class UnitRestatement:
    """Units to add to the fund's ledger, with the reason stated in full.

    Carries both dates because "in transit" is a property of a deal *relative to a valuation date*.
    A restatement citing the units alone cannot be checked; one citing the dates can.
    """

    holder_id: str
    units: Decimal
    trade_date: date
    settlement_date: date
    as_of: date
    deal_ids: tuple[str, ...]

    @property
    def rationale(self) -> str:
        deals = ", ".join(self.deal_ids)
        return (
            f"{self.units} units subscribed on {self.trade_date.isoformat()} settle on "
            f"{self.settlement_date.isoformat()}, so at the {self.as_of.isoformat()} valuation point "
            f"the registrar counts them and the fund's ledger does not ({deals}). Both books are "
            f"correct; the difference resolves on settlement."
        )

    @property
    def resolves_itself(self) -> bool:
        """A difference that settles on its own needs recording, not correcting."""
        return self.settlement_date > self.as_of


class NotExplainedByTransit(ValueError):
    """The units in transit do not account for the break, so arithmetic is not the answer."""


def restate(case: RegisterCase) -> UnitRestatement:
    """Explain a holder-balance break from the deals in transit, or refuse to.

    Refusing matters more than succeeding. If the in-transit units do not equal the difference, this
    break is something else -- a genuine registrar error, a missed transfer -- and reporting a
    confident arithmetic explanation for it would be the same defect as a model inventing one.
    """
    item = case.breaks[0]
    transit = [
        deal
        for deal in register.in_transit(case.fund_id, case.as_of)
        if deal.holder_id == item.holder_id
    ]
    if not transit:
        raise NotExplainedByTransit(
            f"no deals are in transit for {item.holder_id} at {case.as_of.isoformat()}, so the "
            f"{item.difference} unit difference is not a timing difference."
        )

    units = sum((deal.units for deal in transit), Decimal(0))
    if abs(units - item.difference) > item.tolerance_applied:
        raise NotExplainedByTransit(
            f"{units} units are in transit but the books differ by {item.difference}. The "
            f"remaining {item.difference - units} is not explained by timing and needs a human."
        )

    return UnitRestatement(
        holder_id=item.holder_id or "",
        units=units,
        trade_date=min(deal.trade_date for deal in transit),
        settlement_date=min(deal.settlement_date for deal in transit),
        as_of=case.as_of,
        deal_ids=tuple(sorted(deal.deal_id for deal in transit)),
    )
