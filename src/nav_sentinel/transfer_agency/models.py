"""What a share register holds. Units, not money.

Deliberately unrelated to `domain.models`: the two processes share the control plane and nothing
else. If transfer agency imported a fund-accounting type, the "second process" claim would be a
second *view* of the first.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from nav_sentinel.control_plane.governance import CaseBrief, CaseFacts, Impact


class DealType(StrEnum):
    SUBSCRIPTION = "subscription"
    REDEMPTION = "redemption"
    TRANSFER = "transfer"


class RegisterBreakType(StrEnum):
    """What can disagree between the registrar and the fund's own unit ledger."""

    UNITS_IN_ISSUE = "units_in_issue"
    HOLDER_BALANCE = "holder_balance"


class Deal(BaseModel):
    """One instruction on the register."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deal_id: str
    fund_id: str
    holder_id: str
    deal_type: DealType
    #: When the investor dealt, which is what the register recognises.
    trade_date: date
    #: When the money settles. A subscription dealt before the cut-off but unsettled at the
    #: valuation point is *in transit*: the registrar counts the units, the fund's ledger does not.
    settlement_date: date
    units: Decimal
    #: Whose record this is: `registrar` or `fund_accounting`.
    source: str


class HolderPosition(BaseModel):
    """A unit holder's balance on one date, per book."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fund_id: str
    holder_id: str
    as_of: date
    units: Decimal
    source: str


class RegisterBreak(BaseModel):
    """A mechanical disagreement in units. Produced by arithmetic, not by a model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    break_id: str
    fund_id: str
    as_of: date
    break_type: RegisterBreakType
    holder_id: str | None = None
    registrar_units: Decimal
    ledger_units: Decimal
    tolerance_applied: Decimal = Decimal("0.0001")
    note: str = ""

    @property
    def difference(self) -> Decimal:
        """Registrar minus ledger. Positive means the register counts units the fund does not."""
        return self.registrar_units - self.ledger_units


class RegisterCase(BaseModel):
    """The unit of work. The transfer-agency counterpart of an exception case."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    fund_id: str
    as_of: date
    capability: str = "ta.unclassified"
    status: str = "open"
    breaks: list[RegisterBreak] = Field(default_factory=list)
    #: Magnitude in units, which is what this process measures. The control plane is handed this
    #: with its unit attached and derives the band itself.
    units_at_risk: Decimal | None = None
    severity: str | None = None
    approval_class: str | None = None

    @property
    def recurrence_key(self) -> str:
        holders = sorted({b.holder_id for b in self.breaks if b.holder_id})
        return f"{self.fund_id}:holder:{holders[0]}" if holders else f"{self.fund_id}:fund"

    def to_facts(self) -> CaseFacts:
        """Hand the control plane exactly what it is permitted to know.

        The counterpart of the fund-accounting `to_facts`, and the thing that makes the units
        banding real rather than merely demonstrated. That the control plane can band a units
        magnitude was proved by a test building these `CaseFacts` by hand -- which showed the
        platform *could* do it, not that this process ever asked it to. The magnitude goes over with
        its unit attached and the band comes back derived; nothing here computes one.
        """
        return CaseFacts(
            case_id=self.case_id,
            subject_id=self.fund_id,
            as_of=self.as_of,
            capability=self.capability,
            impact=(
                Impact(value=self.units_at_risk, unit="units")
                if self.units_at_risk is not None
                else None
            ),
            status=self.status,
            severity=self.severity,
            item_count=len(self.breaks),
            recurrence_key=self.recurrence_key,
        )

    def to_brief(self) -> CaseBrief:
        """Hand an investigator exactly what it is permitted to know.

        The same flat value the fund-accounting side produces, describing a different kind of break:
        two unit counts and a holder, where a fund case has an accounting value, a custodian value
        and an ISIN. That difference is the whole argument for the breaks arriving as prose -- this
        process renders its own, and the investigator it is handed to is the same code, unchanged.

        Units are rendered plainly and never as money. A register break of 125,000 is 125,000
        *units*; labelling it with a currency would be a false statement in the one place a model is
        most likely to believe it.
        """
        return CaseBrief(
            case_id=self.case_id,
            subject_id=self.fund_id,
            as_of=self.as_of,
            capability=self.capability,
            breaks="\n".join(
                f"  - {b.break_type.value}: holder {b.holder_id}, registrar "
                f"{b.registrar_units} units, fund ledger {b.ledger_units} units, "
                f"difference {b.difference} units"
                + (f" ({b.note})" if b.note else "")
                for b in self.breaks
            ),
        )
