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
