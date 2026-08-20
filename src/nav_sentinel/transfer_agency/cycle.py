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
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from nav_sentinel.control_plane.governance import CaseBrief, CaseFacts
from nav_sentinel.transfer_agency import remediation, tolerance
from nav_sentinel.transfer_agency.models import RegisterCase

#: What the root must supply: something that takes a brief and returns a verdict-like object.
#: Typed loosely on purpose -- `Verdict` lives in the agents layer, which this package cannot see.
Investigate = Callable[[CaseBrief, str | None], Awaitable[Any]]

#: Whether the registry publishes an agent for a capability. Injected for the same reason
#: `Investigate` is: this package cannot import `registry`, and it must not answer the question
#: itself. The first version of this file defaulted to the literal
#: `frozenset({"ta.subscription_in_transit"})`, which made the cycle a second source of truth for
#: routing -- so publishing an agent for `ta.redemption_unsettled` would have left it refused here
#: while `make registry` showed it handled. Dispatch is the registry's decision everywhere else in
#: this codebase, and a hardcoded copy is how that becomes advisory.
Routes = Callable[[str], bool]

#: Opens the audit record for one case and yields `(span, trace_id, band)`. Injected for the same
#: reason the other two are -- this package may not import `control_plane.audit` -- and required
#: rather than optional, because the first version of this file simply had no audit record at all:
#: no root span, no `nav.case.*` attributes, and `trace_id=None` on every observation the second
#: process recorded. On a project whose thesis is that the audit trail is the deliverable, the
#: second process was the one path that produced none.
Trace = Callable[[CaseFacts], AbstractContextManager[tuple[Any, str | None, str]]]


def classify(case: RegisterCase) -> RegisterCase:
    """Assign the capability from the data, without a model.

    `detect()` leaves every case at `ta.unclassified`, which is honest but useless: until something
    sets the capability the registry has nothing to route on, and `register-investigator` sits
    published and unreachable while `make registry` prints it beside a capability as though it were
    handled.

    This asks what *kind* of break it is, not whether the arithmetic closes. Those are different
    questions and merging them was a bug: the first version re-derived `restate`'s exact predicate
    -- same filter, same sum, same tolerance -- so `restate`'s refusal branch became unreachable and
    the one sentence that tells a human what is left over could never be printed. A break with
    subscriptions in transit *is* a subscription-in-transit case whether or not they fully account
    for it; the investigator investigates it and the arithmetic then confirms or refuses.
    """
    transit = remediation.transit_for(case)
    if not transit:
        return case.model_copy(update={"capability": "ta.unclassified"})

    try:
        net = sum((remediation.signed_units(deal) for deal in transit), Decimal(0))
    except remediation.UnsignableDeal:
        # A transfer, whose direction this register does not record. Not a capability this process
        # can claim, and the refusal text belongs to `restate`.
        return case.model_copy(update={"capability": "ta.unclassified"})

    if net > 0:
        # The registrar counts units the ledger does not: recognised on trade date, unsettled.
        capability = "ta.subscription_in_transit"
    elif net < 0:
        # Struck off the register, still on the ledger until settlement.
        capability = "ta.redemption_unsettled"
    else:
        # Deals in transit that net to nothing cannot explain a non-zero difference.
        capability = "ta.unclassified"
    return case.model_copy(update={"capability": capability})


@dataclass(frozen=True)
class CycleResult:
    """What one cycle produced, per case."""

    case: RegisterCase
    #: Derived by the control plane from a unit-tagged magnitude, taken from the audit record rather
    #: than computed again here -- a second derivation would record two identical P-004 decisions.
    band: str | None = None
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
    routes: Routes,
    trace: Trace,
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

        # Every case is traced, including one nothing can handle. A refusal that leaves no audit
        # record is the least reviewable outcome the system can produce.
        with trace(case.to_facts()) as (_span, trace_id, band):
            if not routes(case.capability):
                results.append(
                    CycleResult(
                        case=case, band=band, refused=f"no agent handles {case.capability}"
                    )
                )
                continue

            verdict = await investigate(case.to_brief(), trace_id)
            try:
                restatement = remediation.restate(case)
            except remediation.NotExplainedByTransit as exc:
                # Classification said what kind of break this is; the arithmetic decides whether it
                # closes. This branch is reachable precisely because those are separate questions.
                results.append(
                    CycleResult(case=case, band=band, verdict=verdict, refused=str(exc))
                )
                continue
            results.append(
                CycleResult(case=case, band=band, verdict=verdict, restatement=restatement)
            )
    return results
