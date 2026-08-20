"""The NAV cycle: grouping breaks into cases, and proving the reconciliation is complete.

The central idea. A NAV-per-share difference is not another break to investigate -- it is
the *control total*. Every other break is a candidate explanation for it. The cycle is
complete when the signed sum of the explained cases equals the NAV difference and the
residual is zero.

That gives the fleet an externally checkable definition of "done". A model cannot declare
victory; the arithmetic either closes or it does not.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from nav_sentinel.domain.materiality import ToBase, identity_to_base
from nav_sentinel.domain.models import (
    BreakType,
    ExceptionCase,
    NavRecord,
    ReconciliationBreak,
)

CLOSE_TOLERANCE = Decimal("1.00")  # base currency; rounding across many lines


def _case_id(fund_id: str, as_of: date, key: tuple[str, str]) -> str:
    """Derived from what the case *is*, not from how many have been made.

    `next(_case_counter)` was a process-global `itertools.count`, so the same cycle run twice in one
    process produced different ids -- 0001..0007, then 0008..0014, then 0019. Break ids were
    content-hashed for exactly this reason and case ids were missed, which S8a's byte-identical
    criterion would have failed on.

    The grouping key is the identity: one case per security or per currency per fund per date, which
    is precisely how the buckets are formed. Readable rather than hashed, because a case id appears
    in the governance log, the approval console and the trace, and a reviewer quoting
    `CASE-MERID-GEF-2026-08-17-security-US0378331005` can find it.
    """
    kind, value = key
    return f"CASE-{fund_id}-{as_of.isoformat()}-{kind}-{value or 'none'}"


def group_into_cases(
    breaks: list[ReconciliationBreak], fund_id: str, as_of: date
) -> list[ExceptionCase]:
    """Group mechanical breaks into investigable cases.

    Breaks on the same security belong to one case: a quantity difference and the market
    value difference it causes share a single root cause, and splitting them would send two
    investigators after the same answer.

    NAV-per-share breaks are deliberately excluded -- they are the control total, not work.
    """
    buckets: dict[tuple[str, str], list[ReconciliationBreak]] = {}
    for b in breaks:
        if b.fund_id != fund_id or b.break_type == BreakType.NAV_PER_SHARE:
            continue
        key = ("security", b.isin) if b.isin else ("cash", b.currency or "")
        buckets.setdefault(key, []).append(b)

    cases: list[ExceptionCase] = []
    for key in sorted(buckets):
        cases.append(
            ExceptionCase(
                case_id=_case_id(fund_id, as_of, key),
                fund_id=fund_id,
                as_of=as_of,
                breaks=buckets[key],
                recurrence_key=f"{fund_id}:{key[0]}:{key[1]}",
            )
        )
    return cases


def signed_impact_base(case: ExceptionCase, to_base: ToBase = identity_to_base) -> Decimal:
    """Signed base-currency impact of a case: accounting minus custodian.

    Positive means the accounting book is overstated relative to the custodian.
    """
    total = Decimal(0)
    for b in case.value_breaks:
        ccy = b.value_currency
        total += b.difference if ccy is None else to_base(b.difference, ccy)
    return total


class NavCycle(BaseModel):
    """One fund, one valuation date: the control total and the running explanation."""

    fund_id: str
    as_of: date
    accounting_nav: NavRecord
    custodian_nav: NavRecord
    cases: list[ExceptionCase] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def control_total(self) -> Decimal:
        """The difference the fleet has to explain, in base currency."""
        return self.accounting_nav.net_assets - self.custodian_nav.net_assets

    @property
    def nav_per_share_difference(self) -> Decimal:
        return self.accounting_nav.nav_per_share - self.custodian_nav.nav_per_share

    def explained_total(self, to_base: ToBase = identity_to_base) -> Decimal:
        return sum(
            (signed_impact_base(c, to_base) for c in self.cases), Decimal(0)
        )

    def residual(self, to_base: ToBase = identity_to_base) -> Decimal:
        """Unexplained remainder. Must reach zero before a cycle can be signed off."""
        return self.control_total - self.explained_total(to_base)

    def is_complete(self, to_base: ToBase = identity_to_base) -> bool:
        return abs(self.residual(to_base)) <= CLOSE_TOLERANCE

    def summary(self, to_base: ToBase = identity_to_base) -> dict:
        return {
            "fund_id": self.fund_id,
            "as_of": self.as_of.isoformat(),
            "base_currency": None,
            "control_total": str(self.control_total.quantize(Decimal("0.01"))),
            "nav_per_share_difference": str(self.nav_per_share_difference.quantize(Decimal("0.000001"))),
            "cases": len(self.cases),
            "explained": str(self.explained_total(to_base).quantize(Decimal("0.01"))),
            "residual": str(self.residual(to_base).quantize(Decimal("0.01"))),
            "complete": self.is_complete(to_base),
        }
