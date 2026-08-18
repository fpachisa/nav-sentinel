"""Domain model for fund accounting reconciliation.

Deliberately narrow: this models the *books and records* of a fund plus the exceptions
raised when the accounting book disagrees with the custodian book. Nothing here knows
about agents -- the fleet operates on these types.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- enums


class BreakCategory(StrEnum):
    """Root-cause families. Each maps to exactly one specialist investigator in the
    Agent Registry, which is how Triage performs capability-based routing."""

    CORPORATE_ACTION = "corporate_action"
    FX_RATE = "fx_rate"
    PRICING = "pricing"
    SETTLEMENT = "settlement"
    CASH_FEES = "cash_fees"
    UNCLASSIFIED = "unclassified"


class BreakType(StrEnum):
    """What kind of quantity disagrees."""

    POSITION_QUANTITY = "position_quantity"
    MARKET_VALUE = "market_value"
    CASH_BALANCE = "cash_balance"
    NAV_PER_SHARE = "nav_per_share"


class Severity(StrEnum):
    INFORMATIONAL = "informational"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ExceptionStatus(StrEnum):
    OPEN = "open"
    TRIAGED = "triaged"
    UNDER_INVESTIGATION = "under_investigation"
    ROOT_CAUSE_PROPOSED = "root_cause_proposed"
    REMEDIATION_PROPOSED = "remediation_proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_CLEARED = "auto_cleared"
    ESCALATED = "escalated"


class ApprovalClass(StrEnum):
    """Determined by materiality. Enforced at the Agent Gateway, not inside the agents."""

    AUTO_CLEAR = "auto_clear"
    SINGLE_REVIEWER = "single_reviewer"
    FOUR_EYES = "four_eyes"
    CIO_ESCALATION = "cio_escalation"


# ------------------------------------------------------------------ books & records


class Security(BaseModel):
    isin: str
    ticker: str | None = None
    name: str
    currency: str
    country: str
    security_type: str = "equity"
    # ADRs and depositary receipts are the single richest source of corporate-action
    # breaks, because gross-vs-net dividend treatment differs between custodians.
    is_depositary_receipt: bool = False
    dr_ratio: str | None = None


class Fund(BaseModel):
    fund_id: str
    name: str
    base_currency: str
    domicile: str
    shares_outstanding: Decimal
    fee_bps_annual: Decimal = Decimal(75)


class Position(BaseModel):
    fund_id: str
    isin: str
    as_of: date
    quantity: Decimal
    local_price: Decimal
    local_currency: str
    fx_rate: Decimal
    market_value_base: Decimal
    source: str  # "accounting" | "custodian"


class Trade(BaseModel):
    trade_id: str
    fund_id: str
    isin: str
    trade_date: date
    settlement_date: date
    side: str  # BUY | SELL
    quantity: Decimal
    price: Decimal
    currency: str
    status: str = "settled"  # settled | pending | failed


class CashMovement(BaseModel):
    movement_id: str
    fund_id: str
    value_date: date
    currency: str
    amount: Decimal
    movement_type: str  # dividend | fee | subscription | redemption | settlement | interest
    description: str = ""
    source: str = "accounting"


class NavRecord(BaseModel):
    fund_id: str
    as_of: date
    total_assets_base: Decimal
    total_liabilities_base: Decimal
    shares_outstanding: Decimal
    source: str  # "accounting" | "custodian"

    @property
    def net_assets(self) -> Decimal:
        return self.total_assets_base - self.total_liabilities_base

    @property
    def nav_per_share(self) -> Decimal:
        if self.shares_outstanding == 0:
            return Decimal(0)
        return self.net_assets / self.shares_outstanding


# ---------------------------------------------------------------------- exceptions


class EvidenceItem(BaseModel):
    """One piece of support for a hypothesis. Every hypothesis must cite evidence, and
    every piece of external evidence records whether Model Armor cleared it."""

    source: str  # e.g. "ecb_fx_reference_rates", "sec_edgar", "books_and_records"
    source_uri: str | None = None
    retrieved_at: datetime | None = None
    summary: str
    trusted: bool = True
    armor_verdict: str | None = None  # set when source is untrusted external content


class ReconciliationBreak(BaseModel):
    """A raw, mechanical disagreement. Produced by tolerance rules, not by a model."""

    break_id: str
    fund_id: str
    as_of: date
    break_type: BreakType
    isin: str | None = None
    currency: str | None = None
    accounting_value: Decimal
    custodian_value: Decimal
    tolerance_applied: Decimal

    @property
    def difference(self) -> Decimal:
        return self.accounting_value - self.custodian_value

    @property
    def abs_difference(self) -> Decimal:
        return abs(self.difference)

    @property
    def carries_value(self) -> bool:
        """Whether this break's difference is a monetary amount.

        A quantity break carries no value of its own -- its monetary consequence appears
        in the accompanying market-value break, so counting both would double-count.
        A NAV-per-share break is monetary but *per share*, so it is measured separately.
        """
        return self.break_type in (BreakType.MARKET_VALUE, BreakType.CASH_BALANCE)

    @property
    def value_currency(self) -> str | None:
        """Currency of the difference. Market values are already in fund base currency;
        cash breaks are struck in their own currency and must be converted."""
        if self.break_type == BreakType.MARKET_VALUE:
            return None  # already base
        if self.break_type == BreakType.CASH_BALANCE:
            return self.currency
        return None


class RootCauseHypothesis(BaseModel):
    category: BreakCategory
    statement: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    investigator_agent: str | None = None
    investigator_version: str | None = None


class JournalEntryLine(BaseModel):
    account: str
    currency: str
    debit: Decimal = Decimal(0)
    credit: Decimal = Decimal(0)
    narrative: str = ""


class RemediationProposal(BaseModel):
    """A *proposal*. No agent in the fleet is permitted to post one; the Agent Gateway
    rejects any attempt to commit without a recorded human approval."""

    proposal_id: str
    lines: list[JournalEntryLine]
    expected_residual: Decimal
    rationale: str
    proposed_by_agent: str
    proposed_by_version: str
    requires: ApprovalClass

    @property
    def balances(self) -> bool:
        return sum(l.debit for l in self.lines) == sum(l.credit for l in self.lines)


class ExceptionCase(BaseModel):
    """The unit of work that flows through the fleet. Its audit trail is the deliverable."""

    case_id: str
    fund_id: str
    as_of: date
    status: ExceptionStatus = ExceptionStatus.OPEN
    breaks: list[ReconciliationBreak] = Field(default_factory=list)

    nav_impact_bps: float | None = None
    severity: Severity | None = None
    approval_class: ApprovalClass | None = None

    category: BreakCategory = BreakCategory.UNCLASSIFIED
    hypotheses: list[RootCauseHypothesis] = Field(default_factory=list)
    proposal: RemediationProposal | None = None

    # Cross-cycle memory: populated from Memory Bank when this break has been seen before.
    recurrence_key: str | None = None
    prior_occurrences: int = 0

    trace_id: str | None = None
    created_at: datetime | None = None

    @property
    def leading_hypothesis(self) -> RootCauseHypothesis | None:
        if not self.hypotheses:
            return None
        return max(self.hypotheses, key=lambda h: h.confidence)

    @property
    def value_breaks(self) -> list[ReconciliationBreak]:
        return [b for b in self.breaks if b.carries_value]

    @property
    def quantity_breaks(self) -> list[ReconciliationBreak]:
        return [b for b in self.breaks if b.break_type == BreakType.POSITION_QUANTITY]

    @property
    def nav_per_share_breaks(self) -> list[ReconciliationBreak]:
        return [b for b in self.breaks if b.break_type == BreakType.NAV_PER_SHARE]
