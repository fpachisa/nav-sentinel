"""Materiality scoring and approval routing.

Deterministic, on purpose: which control applies to a given adjustment is a governance
decision that must be reproducible and testable, never a model's judgement. The fleet's
models explain the break; policy decides who is permitted to clear it.

Units matter here. A position-quantity break carries no monetary value of its own -- its
consequence shows up in the accompanying market-value break -- and a NAV-per-share break
is monetary but expressed per share. Summing those together would be meaningless, so each
is measured in its own terms and the most severe measure governs the case.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Callable, Protocol

from nav_sentinel.config import settings
from nav_sentinel.domain.models import ApprovalClass, ExceptionCase, NavRecord, Severity

_BPS = Decimal("10000")

# Converts an amount in `currency` into the fund's base currency. Injected rather than
# imported so that scoring stays a pure function and remains unit-testable offline.
ToBase = Callable[[Decimal, str], Decimal]


def identity_to_base(amount: Decimal, currency: str) -> Decimal:
    return amount


def value_impact_base(case: ExceptionCase, to_base: ToBase) -> Decimal:
    """Total monetary impact of the case, in fund base currency."""
    total = Decimal("0")
    for b in case.value_breaks:
        ccy = b.value_currency
        total += b.abs_difference if ccy is None else abs(to_base(b.difference, ccy))
    return total


def nav_impact_bps(difference: Decimal, nav: NavRecord) -> float:
    """Express a base-currency difference as basis points of fund net assets."""
    net = nav.net_assets
    if net == 0:
        return 0.0
    return float(abs(difference) / net * _BPS)


def nav_per_share_impact_bps(difference: Decimal, nav: NavRecord) -> float:
    """Express a NAV-per-share difference as basis points of NAV per share.

    This is the figure that actually matters to an investor dealing at that price, and
    the one a fund's error-correction policy is written against.
    """
    nps = nav.nav_per_share
    if nps == 0:
        return 0.0
    return float(abs(difference) / nps * _BPS)


def severity_for(bps: float) -> Severity:
    if bps < 0.25:
        return Severity.INFORMATIONAL
    if bps < 1.0:
        return Severity.LOW
    if bps < 5.0:
        return Severity.MEDIUM
    if bps < 20.0:
        return Severity.HIGH
    return Severity.CRITICAL


def approval_class_for(bps: float) -> ApprovalClass:
    s = settings()
    if bps <= s.auto_clear_max_bps:
        return ApprovalClass.AUTO_CLEAR
    if bps < s.four_eyes_min_bps:
        return ApprovalClass.SINGLE_REVIEWER
    if bps < s.escalate_cio_min_bps:
        return ApprovalClass.FOUR_EYES
    return ApprovalClass.CIO_ESCALATION


def score(
    case: ExceptionCase,
    nav: NavRecord,
    to_base: ToBase = identity_to_base,
) -> ExceptionCase:
    """Attach materiality, severity and approval class to a case. Idempotent.

    `nav` should be the custodian record: it is the independent third-party book, so it
    is the appropriate denominator when the accounting book is the suspect side.
    """
    measures = [nav_impact_bps(value_impact_base(case, to_base), nav)]
    measures += [nav_per_share_impact_bps(b.abs_difference, nav) for b in case.nav_per_share_breaks]

    bps = max(measures) if measures else 0.0
    case.nav_impact_bps = round(bps, 4)
    case.severity = severity_for(bps)
    case.approval_class = approval_class_for(bps)
    return case
