"""Correcting a register break. No model, and that is the claim rather than a shortcut.

The registrar recognises a deal from its trade date and the fund's ledger from its settlement date,
so between those dates the two books differ by exactly the dealt units. There is no judgement in
that -- it is signed arithmetic against a date.

So this process does not put a language model on the step. A fleet that uses judgement where
judgement is required and deterministic logic where it is not is a better claim than one that puts
an LLM on every step, and the honest way to make that claim is to have a process that demonstrably
does the second thing. Arithmetic earns that claim only while it is *right*, which is the whole
reason for the sign discipline below.

The governance path is unchanged: the correction is still a *proposal*, it is still banded by the
control plane from a unit-tagged magnitude, and nothing here posts anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from nav_sentinel.transfer_agency import register
from nav_sentinel.transfer_agency.models import Deal, DealType, RegisterBreak, RegisterCase


class UnsignableDeal(ValueError):
    """A deal whose effect on one holder's balance this model cannot determine.

    A transfer moves units between two holders and `Deal` carries one `holder_id`, so nothing here
    can say whether this holder is the source or the destination. Refusing is the only correct
    answer: guessing a sign would produce a confident arithmetic explanation that is wrong half the
    time, which is worse than no explanation at all.
    """


def signed_units(deal: Deal) -> Decimal:
    """A deal's contribution to `registrar_units - ledger_units` while it is in transit.

    The sign is the whole correctness argument. A subscription is recognised by the registrar first,
    so it makes the registrar *larger* than the ledger: positive. A redemption is struck off the
    register first, so it makes the registrar *smaller*: negative. Summing both with a uniform `+`
    -- which this module did -- made a redemption in transit report `abs(125000 - (-125000))` =
    250,000 units unexplained, and then told a human "the remaining -250000 is not explained by
    timing", which is arithmetic nonsense in the one sentence a reviewer actually reads.
    """
    if deal.deal_type is DealType.SUBSCRIPTION:
        return deal.units
    if deal.deal_type is DealType.REDEMPTION:
        return -deal.units
    raise UnsignableDeal(
        f"{deal.deal_id} is a {deal.deal_type.value} and this register models one holder per deal, "
        f"so whether {deal.holder_id} is the source or the destination is not recorded."
    )


class NotASingleHolderBreak(ValueError):
    """This process explains one holder's balance at a time.

    `breaks[0]` was read in three places with nothing constraining the list to one entry, while
    `to_brief` rendered *all* of them into the prompt and `to_facts` reported `item_count`. So a
    two-break case would have been described to the model in full and then explained from the first
    break alone -- a correction that looks complete and covers part of the case.
    """


def only_break(case: RegisterCase) -> RegisterBreak:
    """The one break this case is about, or a refusal. One definition, used by every caller."""
    if len(case.breaks) != 1:
        raise NotASingleHolderBreak(
            f"{case.case_id} carries {len(case.breaks)} breaks. This process corrects one holder "
            f"balance at a time, and explaining the first while describing all of them would "
            f"understate the case."
        )
    return case.breaks[0]


def transit_for(case: RegisterCase) -> list[Deal]:
    """The deals in transit for this case's holder. One definition, used by both callers."""
    item = only_break(case)
    return [
        deal
        for deal in register.in_transit(case.fund_id, case.as_of)
        if deal.holder_id == item.holder_id
    ]


@dataclass(frozen=True)
class TransitLeg:
    """One in-transit deal, with its own dates. Never merged with another."""

    deal_id: str
    deal_type: str
    #: Signed, per `signed_units`.
    units: Decimal
    trade_date: date
    settlement_date: date

    def __str__(self) -> str:
        return (
            f"{self.deal_id} ({self.deal_type}) {self.units} units, traded "
            f"{self.trade_date.isoformat()}, settling {self.settlement_date.isoformat()}"
        )


@dataclass(frozen=True)
class UnitRestatement:
    """Units to add to the fund's ledger, with the reason stated in full.

    Holds the legs rather than a single date pair. The previous version carried one `trade_date` and
    one `settlement_date` taken as `min()` across every deal, which for two subscriptions -- 25,000
    settling on the 30th and 100,000 settling on the 18th -- produced "125,000 units subscribed on
    the 10th settle on the 18th": a (units, trade, settlement) triple belonging to no deal, telling
    a reviewer the whole difference clears on the 18th when a fifth of it does not. The docstring
    justifying those fields said a restatement citing dates can be checked, and aggregation had made
    them uncheckable.
    """

    holder_id: str
    #: The net signed difference the legs account for.
    units: Decimal
    as_of: date
    legs: tuple[TransitLeg, ...]

    @property
    def deal_ids(self) -> tuple[str, ...]:
        return tuple(leg.deal_id for leg in self.legs)

    @property
    def clears_on(self) -> date:
        """When the *whole* difference has cleared -- the last settlement, not the first."""
        return max(leg.settlement_date for leg in self.legs)

    @property
    def resolves_itself(self) -> bool:
        """A difference that settles on its own needs recording, not correcting.

        Always true for a restatement built by `restate`, because `in_transit` only returns deals
        settling after the valuation point -- so the test asserting it was comparing a constant to a
        constant. It is kept because the dataclass is constructible from any legs and the semantics
        are what a reader needs, and it is now tested on both branches directly.
        """
        return self.clears_on > self.as_of

    @property
    def rationale(self) -> str:
        if len(self.legs) == 1:
            leg = self.legs[0]
            # Which book is ahead depends on the direction, and saying it backwards would be a
            # false statement in the sentence a reviewer actually checks. A subscription is on the
            # register first; a redemption is off the register first and still on the ledger.
            subscription = leg.units > 0
            verb = "subscribed" if subscription else "redeemed"
            which = (
                "the registrar counts them and the fund's ledger does not"
                if subscription
                else "the registrar has struck them off and the fund's ledger has not"
            )
            return (
                f"{abs(leg.units)} units {verb} on {leg.trade_date.isoformat()} settle on "
                f"{leg.settlement_date.isoformat()}, so at the {self.as_of.isoformat()} valuation "
                f"point {which} ({leg.deal_id}). Both books are correct; the difference resolves "
                f"on settlement."
            )
        legs = "; ".join(str(leg) for leg in self.legs)
        return (
            f"{self.units} net units across {len(self.legs)} deals in transit at the "
            f"{self.as_of.isoformat()} valuation point: {legs}. Both books are correct; the "
            f"difference is fully cleared after {self.clears_on.isoformat()}."
        )


class NotExplainedByTransit(ValueError):
    """The units in transit do not account for the break, so arithmetic is not the answer."""


def restate(case: RegisterCase) -> UnitRestatement:
    """Explain a holder-balance break from the deals in transit, or refuse to.

    Refusing matters more than succeeding, and this is the *only* place the question is decided.
    `cycle.classify` used to re-derive this exact predicate before routing, which made this
    function's refusal branch structurally unreachable -- so the informative sentence below, naming
    what is left over, could never be produced on any runnable path. Classification now asks a
    different question (what *kind* of break is this), and the arithmetic is settled here alone.
    """
    item = only_break(case)
    transit = transit_for(case)
    if not transit:
        raise NotExplainedByTransit(
            f"no deals are in transit for {item.holder_id} at {case.as_of.isoformat()}, so the "
            f"{item.difference} unit difference is not a timing difference."
        )

    try:
        legs = tuple(
            TransitLeg(
                deal_id=deal.deal_id,
                deal_type=deal.deal_type.value,
                units=signed_units(deal),
                trade_date=deal.trade_date,
                settlement_date=deal.settlement_date,
            )
            for deal in sorted(transit, key=lambda d: (d.trade_date, d.deal_id))
        )
    except UnsignableDeal as exc:
        raise NotExplainedByTransit(
            f"{exc} The difference of {item.difference} units may be timing, but this process "
            f"cannot prove it, so it needs a human."
        ) from exc

    net = sum((leg.units for leg in legs), Decimal(0))
    if abs(net - item.difference) > item.tolerance_applied:
        raise NotExplainedByTransit(
            f"{net} net units are in transit but the books differ by {item.difference}. The "
            f"remaining {item.difference - net} is not explained by timing and needs a human."
        )

    return UnitRestatement(
        holder_id=item.holder_id or "",
        units=net,
        as_of=case.as_of,
        legs=legs,
    )
