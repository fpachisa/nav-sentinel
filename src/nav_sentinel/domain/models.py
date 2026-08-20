"""Domain model for fund accounting reconciliation.

Deliberately narrow: this models the *books and records* of a fund plus the exceptions
raised when the accounting book disagrees with the custodian book. Nothing here knows
about agents -- the fleet operates on these types.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Governance vocabulary belongs to the control plane, not to fund accounting. Imported here so
# the domain speaks it; defined there so the control plane never imports a domain type.
from nav_sentinel.control_plane.governance import (
    ApprovalClass,
    CaseBrief,
    CaseFacts,
    Impact,
)

# --------------------------------------------------------------------------- enums


class BreakCategory(StrEnum):
    """Root-cause families, in this process's own vocabulary.

    Crossing the seam they become namespaced capability strings (`nav.fx_rate`) via
    `capability`, so a second process cannot collide on a bare category name. The registry no
    longer holds this enum — it could never have routed for a second process.
    """

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


#: Accounts whose movement changes net assets. A correction's effect on NAV is the sum of its legs
#: touching these, which is what the golden's `expected_corrections` state -- the contra leg is
#: required for the entry to balance but does not move NAV.
NAV_ACCOUNTS = frozenset(
    {"investments_at_market", "cash_at_bank", "dividends_receivable", "accrued_expenses"}
)


# ---------------------------------------------------------------------- exceptions


class ObservedFacts(BaseModel):
    """The facts a tool actually returned, typed, in the vocabulary the golden file cites.

    Field names are the golden's `evidence_must_cite` entries verbatim -- `rate`, `rate_date`,
    `gross_rate` -- so a scenario's stated evidence requirement can be checked against a verdict
    by name instead of through a mapping nobody maintains.

    Every field here is populated by the code that made the tool call, from the value it returned.
    **Never by a model.** That is the whole point: an investigator citing "the ECB rate for the
    14th" proves nothing if it also supplies the rate, and a rate date expressed as free text can
    only be checked with a regex over model prose.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rate: Decimal | None = None
    rate_date: date | None = None
    gross_rate: Decimal | None = None
    withholding_pct: Decimal | None = None
    split_ratio: str | None = None
    #: Which document the evidence was read from. A citable fact rather than mere metadata because
    #: the golden's `evidence_must_cite` names it: for a corporate action, *which filing you read*
    #: is the first question a reviewer asks, and a verdict that cannot answer it has not shown its
    #: working.
    filing: str | None = None
    quantity: Decimal | None = None
    amount: Decimal | None = None
    currency: str | None = None
    #: Country of domicile. Declared because the corporate-action cross-check turns on it -- a
    #: dividend's expected withholding follows the issuer's domicile -- and because the security
    #: projection was already producing it while nothing here declared it, so the fact was recorded
    #: and then silently dropped on the way back.
    domicile: str | None = None
    as_of: date | None = None

    @classmethod
    def from_recorded(cls, observed: dict[str, str]) -> ObservedFacts:
        """Rebuild the typed facts from what the platform stored as text.

        The platform records observations as opaque strings so it need not know this vocabulary --
        see `control_plane.observations`. Rebuilding them is therefore the process's job, and keys
        this process does not recognise are ignored rather than raising: a pack may project extra
        facts for its own audit trail without every consumer knowing them.
        """
        known = {k: v for k, v in observed.items() if k in cls.model_fields}
        return cls.model_validate(known)

    def cited(self) -> frozenset[str]:
        """Which facts this observation actually carries.

        A golden scenario's `evidence_must_cite` is satisfied when its entries are a subset of the
        union of these across a verdict's evidence.
        """
        return frozenset(
            name for name, value in self.model_dump().items() if value is not None
        )


class EvidenceItem(BaseModel):
    """One piece of support for a hypothesis. Every hypothesis must cite evidence, and
    every piece of external evidence records whether Model Armor cleared it."""

    source: str  # e.g. "ecb_fx_reference_rates", "sec_edgar", "books_and_records"
    source_uri: str | None = None
    retrieved_at: datetime | None = None
    summary: str
    trusted: bool = True
    armor_verdict: str | None = None  # set when source is untrusted external content

    #: The tool call this item stands on, and what it returned. Both are set by the platform from
    #: a recorded observation, never accepted from a model -- see `agents.contract`.
    tool: str | None = None
    observed: ObservedFacts | None = None


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
    #: Set when the break needs stating rather than just measuring -- a record present on one
    #: side only, or a quantity that makes the usual comparison undefined.
    note: str = ""

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


class Outcome(StrEnum):
    """What a correction actually is. Not every break is fixed by a journal.

    Four of the six seeded scenarios are journals; two are not, and forcing them into a journal
    shape would have produced a fabricated entry for a break that needs none.
    """

    #: Debits and credits. Balances per currency.
    JOURNAL_ENTRY = "journal_entry"
    #: The books are both right and the difference is timing -- a trade recognised on trade date by
    #: one side and settlement date by the other. There is nothing to post; the item is stated,
    #: explained and carried. Drafting an entry here would create an error rather than fix one.
    RECONCILING_ITEM = "reconciling_item"
    #: A share count is wrong and no value moves: an unapplied split changes quantity while market
    #: value agrees exactly. A journal cannot express it, because there is no amount.
    QUANTITY_RESTATEMENT = "quantity_restatement"


class JournalEntryLine(BaseModel):
    account: str
    currency: str
    debit: Decimal = Decimal(0)
    credit: Decimal = Decimal(0)
    narrative: str = ""

    @property
    def signed(self) -> Decimal:
        """Debit positive, credit negative -- the effect on the account."""
        return self.debit - self.credit


class QuantityRestatementLine(BaseModel):
    """A share count corrected, with no money involved.

    Carries both counts rather than a delta: a restatement a reviewer cannot check against the
    books is not reviewable, and "from 96,000 to 192,000" is checkable where "+96,000" is not.
    """

    account: str
    isin: str
    from_quantity: Decimal
    to_quantity: Decimal
    narrative: str = ""

    @property
    def delta(self) -> Decimal:
        return self.to_quantity - self.from_quantity


class RemediationProposal(BaseModel):
    """A *proposal*. No agent in the fleet is permitted to post one; the Agent Gateway
    rejects any attempt to commit without a recorded human approval."""

    #: Extras forbidden, like every other model at a boundary here. This was the one place on the
    #: posting path that silently discarded an unknown field, found when a test misspelled
    #: `quantity_lines` as `quantity_restatements` and pydantic accepted the proposal without the
    #: restatement -- which is precisely how a correction gets approved for less than it claims.
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    outcome: Outcome = Outcome.JOURNAL_ENTRY
    lines: list[JournalEntryLine] = Field(default_factory=list)
    quantity_lines: list[QuantityRestatementLine] = Field(default_factory=list)
    expected_residual: Decimal
    rationale: str
    proposed_by_agent: str
    proposed_by_version: str
    requires: ApprovalClass

    @property
    def balances_by_currency(self) -> dict[str, Decimal]:
        """Net debit minus credit, per currency. Every entry must be zero."""
        totals: dict[str, Decimal] = {}
        for line in self.lines:
            totals[line.currency] = totals.get(line.currency, Decimal(0)) + line.signed
        return totals

    @property
    def balances(self) -> bool:
        """Balanced in **every** currency it touches.

        Summing all debits against all credits regardless of currency let a proposal with a USD leg
        and an EUR leg net to zero while balancing in neither -- an entry no ledger would accept,
        passing the only arithmetic check there was.
        """
        return all(total == 0 for total in self.balances_by_currency.values())

    @property
    def nav_legs(self) -> list[tuple[str, str | None, Decimal]]:
        """The individual legs that move net assets, as `(account, currency, signed amount)`.

        Legs, not a per-currency net. The golden's `expected_corrections` state each affected leg
        separately -- the failed trade lists securities and cash as two -- and S5's criterion is
        *leg-level* accuracy. Netting per currency collapsed those two to zero, which would have
        scored a correct two-leg entry as having no effect at all.

        A journal's contra leg is excluded because it is a P&L or equity line rather than an asset
        or liability, so it does not itself appear in net assets: an FX correction reducing
        investments by 86,625.48 lowers NAV by exactly that, and the golden states one leg for it.
        """
        return [
            (line.account, line.currency, line.signed)
            for line in self.lines
            if line.account in NAV_ACCOUNTS
        ]

    @property
    def quantity_legs(self) -> list[tuple[str, str, Decimal]]:
        """Share counts restated, as `(account, isin, delta)`. Separate from `nav_legs` on purpose.

        The quantity branch used to be appended to `nav_legs` in the same tuple shape as money, and
        `stock_record` is not a NAV account -- so a 2:1 split emitted a *share count* of 96,000 into
        a sum of currency amounts. Measured against the golden's own corrections, that made
        `Σ nav_legs` miss `−control_total` by exactly 96,000.0074: the share delta, to the share.
        The closure invariant is the headline number, and it was wrong by a whole leg.

        It also scored a correct restatement as wrong the other way round. The golden states a split
        as `amount: 0.00` **and** `quantity: 96000.0000` in separate fields, so a comparison over
        `(account, currency, amount)` triples saw 96,000 where 0.00 was expected.

        A split moves no net assets -- which is exactly why the golden says `0.00`.
        """
        return [(line.account, line.isin, line.delta) for line in self.quantity_lines]

    @model_validator(mode="after")
    def _the_outcome_matches_what_is_attached(self) -> RemediationProposal:
        """Each outcome has exactly one shape, and mismatches are refused rather than tolerated.

        A reconciling item with journal lines is a contradiction: it says the books are both right
        and then posts a correction. A quantity restatement with an amount says money moved when the
        market values agree exactly. Both were expressible before.
        """
        if self.outcome is Outcome.JOURNAL_ENTRY:
            if not self.lines:
                raise ValueError("a journal entry with no lines corrects nothing")
            if self.quantity_lines:
                raise ValueError(
                    "a journal entry cannot restate a quantity; propose a quantity_restatement"
                )
            unbalanced = {c: t for c, t in self.balances_by_currency.items() if t != 0}
            if unbalanced:
                raise ValueError(
                    f"the entry does not balance in {sorted(unbalanced)}: {unbalanced}. Every "
                    f"currency must net to zero."
                )
        elif self.outcome is Outcome.QUANTITY_RESTATEMENT:
            if not self.quantity_lines:
                raise ValueError("a quantity restatement with no quantity lines restates nothing")
            if self.lines:
                raise ValueError(
                    "a quantity restatement moves no money; a split changes the share count while "
                    "market value agrees exactly"
                )
        elif self.lines or self.quantity_lines:
            raise ValueError(
                "a reconciling item posts nothing: it states that both books are right and the "
                "difference is timing"
            )
        return self


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

    @property
    def capability(self) -> str:
        """This case's category as a namespaced capability string."""
        return f"nav.{self.category.value}"

    def to_facts(self) -> CaseFacts:
        """Hand the control plane exactly what it is permitted to know.

        Deliberately lossy. `fund_id` becomes an opaque `subject_id`, `breaks` becomes a count,
        and the three domain enums become plain strings. The control plane cannot reach back
        through any of them, which is what makes it hostable by a second process.
        """
        return CaseFacts(
            case_id=self.case_id,
            subject_id=self.fund_id,
            as_of=self.as_of,
            capability=self.capability,
            impact=(
                Impact(value=Decimal(str(self.nav_impact_bps)), unit="bps")
                if self.nav_impact_bps is not None
                else None
            ),
            status=self.status.value,
            severity=self.severity.value if self.severity else None,
            item_count=len(self.breaks),
            recurrence_key=self.recurrence_key,
            # A stock-record break does not clear on monetary materiality. The domain knows why;
            # the control plane only needs to know that it must not.
            no_auto_clear=bool(self.quantity_breaks),
        )

    def to_brief(self) -> CaseBrief:
        """Hand an investigator exactly what it is permitted to know.

        Lossy in the same way `to_facts` is, and for the same reason. The break rendering below used
        to live inside `agents/investigator.py`, which meant a supposedly process-agnostic function
        read `accounting_value`, `custodian_value` and `isin` -- fund accounting's vocabulary -- off
        every case it was given. A second process could not describe its own breaks there, so it
        could not use the investigator at all.
        """
        return CaseBrief(
            case_id=self.case_id,
            subject_id=self.fund_id,
            as_of=self.as_of,
            capability=self.capability,
            breaks="\n".join(
                f"  - {b.break_type.value}: accounting {b.accounting_value}, custodian "
                f"{b.custodian_value}, difference {b.difference}"
                + (f", ISIN {b.isin}" if b.isin else "")
                + (f", local currency {b.currency}" if b.currency else "")
                + (f" ({b.note})" if b.note else "")
                for b in self.breaks
            ),
        )
