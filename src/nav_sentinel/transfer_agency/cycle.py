"""One transfer-agency reconciliation cycle: detect, classify, investigate, restate.

**The investigator arrives injected.** This package may not import `nav_sentinel.agents` -- a test
asserts it reaches the platform only through `packs`, `governance` and `gateway` -- so the cycle
declares the one capability it needs as a parameter and the composition root supplies the real
runner. That is not a dodge around the seam; it is the seam working. The process says "I need
something that can investigate a `CaseBrief`", the root decides what that is, and an offline test
supplies a fake without a model or a network.

Classification here is **deterministic and says so**. Whether a holder-balance break coincides with
a deal in transit is a date comparison, and the fund-accounting side runs a model for triage only
because a NAV break's cause genuinely is ambiguous from the numbers alone. Spending a model call
where a comparison suffices would be theatre, and this file would rather be the counter-example.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from nav_sentinel.control_plane.governance import CaseBrief
from nav_sentinel.transfer_agency import register, remediation, tolerance
from nav_sentinel.transfer_agency.models import RegisterCase

#: What the root must supply: something that takes a brief and returns a verdict-like object.
#: Typed loosely on purpose -- `Verdict` lives in the agents layer, which this package cannot see.
Investigate = Callable[[CaseBrief], Awaitable[Any]]


def classify(case: RegisterCase) -> RegisterCase:
    """Assign the capability from the data, without a model.

    `detect()` leaves every case at `ta.unclassified`, which is honest but useless: until something
    sets the capability the registry has nothing to route on, and `register-investigator` sits
    published and unreachable while `make registry` prints it beside a capability as though it were
    handled.
    """
    item = case.breaks[0]
    transit = [
        deal
        for deal in register.in_transit(case.fund_id, case.as_of)
        if deal.holder_id == item.holder_id
    ]
    units = sum((deal.units for deal in transit), Decimal(0))

    if transit and abs(units - item.difference) <= item.tolerance_applied:
        # The registrar counts from trade date and the ledger from settlement date, so between
        # those two dates the books differ by exactly the dealt units and neither is wrong.
        capability = "ta.subscription_in_transit"
    elif transit:
        # Deals are in transit but they do not account for the difference. Partly timing, partly
        # something else -- and "partly something else" is not a capability this process can claim
        # to handle, so it escalates rather than guessing.
        capability = "ta.unclassified"
    else:
        capability = "ta.unclassified"
    return case.model_copy(update={"capability": capability})


@dataclass(frozen=True)
class CycleResult:
    """What one cycle produced, per case."""

    case: RegisterCase
    verdict: Any | None = None
    restatement: remediation.UnitRestatement | None = None
    #: Why no correction was produced, when none was.
    refused: str | None = None

    @property
    def resolved(self) -> bool:
        return self.restatement is not None


async def run(
    fund_id: str,
    as_of: date,
    *,
    investigate: Investigate,
    investigable: frozenset[str] = frozenset({"ta.subscription_in_transit"}),
) -> list[CycleResult]:
    """Reconcile the register and correct what arithmetic can explain.

    The order is the argument. The investigator establishes the cause *from evidence it cites* --
    the pack's P-007 requirement makes it cite units and both dates, so a verdict that cannot
    quote them is refused. Only then does the arithmetic compute the correction. A model is used
    to establish and attribute; it is never used to compute the number.
    """
    results: list[CycleResult] = []
    for detected in tolerance.detect(fund_id, as_of):
        case = classify(detected)

        if case.capability not in investigable:
            results.append(CycleResult(case=case, refused=f"no agent handles {case.capability}"))
            continue

        verdict = await investigate(case.to_brief())
        try:
            restatement = remediation.restate(case)
        except remediation.NotExplainedByTransit as exc:
            # Classification said the transit accounts for it and the arithmetic disagreed. That
            # is a real disagreement, not a formality, so it surfaces rather than being smoothed.
            results.append(CycleResult(case=case, verdict=verdict, refused=str(exc)))
            continue
        results.append(CycleResult(case=case, verdict=verdict, restatement=restatement))
    return results
