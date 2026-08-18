"""Invariants of the deterministic reconciliation core."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from nav_sentinel.domain import tolerance
from nav_sentinel.domain.cycle import NavCycle, group_into_cases, signed_impact_base
from nav_sentinel.domain.materiality import approval_class_for, score, severity_for
from nav_sentinel.domain.models import (
    ApprovalClass,
    BreakType,
    Position,
    Severity,
)
from nav_sentinel.tools import books_and_records as bnr
from nav_sentinel.tools.fx_convert import make_to_base

NAV_DATE = date(2026, 8, 17)


def _pos(fund, isin, qty, mv, source, ccy="USD"):
    return Position(
        fund_id=fund, isin=isin, as_of=NAV_DATE, quantity=Decimal(qty),
        local_price=Decimal("1"), local_currency=ccy, fx_rate=Decimal("1"),
        market_value_base=Decimal(mv), source=source,
    )


class TestPositionAggregation:
    """A fund may hold one security across several lots. Keying rows into a dict would
    silently discard lots and under-report the break, so aggregation is mandatory."""

    def test_multiple_lots_are_summed_not_overwritten(self):
        accounting = [
            _pos("F1", "ISIN1", "100", "1000", "accounting"),
            _pos("F1", "ISIN1", "50", "500", "accounting"),   # second lot
        ]
        custodian = [_pos("F1", "ISIN1", "100", "1000", "custodian")]

        breaks = tolerance.detect_position_breaks(accounting, custodian)
        qty = next(b for b in breaks if b.break_type == BreakType.POSITION_QUANTITY)

        assert qty.accounting_value == Decimal("150"), "lots must aggregate, not overwrite"
        assert qty.difference == Decimal("50")

    def test_no_break_when_lots_sum_to_custodian(self):
        accounting = [
            _pos("F1", "ISIN1", "60", "600", "accounting"),
            _pos("F1", "ISIN1", "40", "400", "accounting"),
        ]
        custodian = [_pos("F1", "ISIN1", "100", "1000", "custodian")]
        assert tolerance.detect_position_breaks(accounting, custodian) == []


class TestMaterialityUnits:
    """Quantity, market value, cash and NAV-per-share are different units and must never
    be summed together."""

    def test_quantity_break_carries_no_value(self):
        breaks = tolerance.detect_position_breaks(
            [_pos("F1", "ISIN1", "150", "1000", "accounting")],
            [_pos("F1", "ISIN1", "100", "1000", "custodian")],
        )
        case = group_into_cases(breaks, "F1", NAV_DATE)[0]
        assert case.quantity_breaks, "expected a quantity break"
        assert case.value_breaks == [], "a quantity difference carries no monetary value"
        assert signed_impact_base(case) == Decimal("0")

    def test_split_has_quantity_break_but_zero_nav_impact(self):
        """A 2:1 split unapplied on one side: quantity differs 2x, value agrees."""
        breaks = tolerance.detect_position_breaks(
            bnr.positions("accounting", "MERID-GEF"), bnr.positions("custodian", "MERID-GEF")
        )
        split = [b for b in breaks if b.isin == "US5949181045"]
        assert len(split) == 1
        assert split[0].break_type == BreakType.POSITION_QUANTITY
        assert split[0].abs_difference == Decimal("96000.0000")

        case = group_into_cases(split, "MERID-GEF", NAV_DATE)[0]
        nav = bnr.nav_record("custodian", "MERID-GEF", NAV_DATE)
        score(case, nav, make_to_base("EUR", NAV_DATE))
        assert case.nav_impact_bps == 0.0
        assert case.approval_class is ApprovalClass.AUTO_CLEAR


class TestApprovalRouting:
    @pytest.mark.parametrize(
        "bps,expected",
        [
            (0.0, ApprovalClass.AUTO_CLEAR),
            (0.25, ApprovalClass.AUTO_CLEAR),
            (0.26, ApprovalClass.SINGLE_REVIEWER),
            (0.99, ApprovalClass.SINGLE_REVIEWER),
            (1.0, ApprovalClass.FOUR_EYES),
            (4.99, ApprovalClass.FOUR_EYES),
            (5.0, ApprovalClass.CIO_ESCALATION),
            (500.0, ApprovalClass.CIO_ESCALATION),
        ],
    )
    def test_thresholds_are_exact(self, bps, expected):
        assert approval_class_for(bps) is expected

    def test_severity_ladder(self):
        assert severity_for(0.1) is Severity.INFORMATIONAL
        assert severity_for(0.5) is Severity.LOW
        assert severity_for(2.0) is Severity.MEDIUM
        assert severity_for(10.0) is Severity.HIGH
        assert severity_for(100.0) is Severity.CRITICAL


class TestNavControlTotal:
    """The reconciliation is complete only when the explained cases account for the whole
    NAV difference. This is the fleet's definition of done, and it is arithmetic."""

    @pytest.mark.parametrize("fund_id,base", [("MERID-GEF", "EUR"), ("ATLAS-USE", "USD")])
    def test_seeded_breaks_fully_explain_the_nav_difference(self, fund_id, base):
        all_breaks = (
            tolerance.detect_position_breaks(bnr.positions("accounting"), bnr.positions("custodian"))
            + tolerance.detect_cash_breaks(
                bnr.cash_movements("accounting"), bnr.cash_movements("custodian")
            )
        )
        to_base = make_to_base(base, NAV_DATE)
        cycle = NavCycle(
            fund_id=fund_id,
            as_of=NAV_DATE,
            accounting_nav=bnr.nav_record("accounting", fund_id, NAV_DATE),
            custodian_nav=bnr.nav_record("custodian", fund_id, NAV_DATE),
            cases=group_into_cases(all_breaks, fund_id, NAV_DATE),
        )
        assert cycle.cases, "expected seeded breaks for this fund"
        assert cycle.is_complete(to_base), (
            f"residual {cycle.residual(to_base)} -- the fleet cannot sign off a cycle it "
            f"has not fully explained"
        )

    def test_nav_per_share_break_is_control_total_not_work(self):
        nav_breaks = tolerance.detect_nav_breaks(
            bnr.nav_records("accounting"), bnr.nav_records("custodian")
        )
        assert nav_breaks, "expected a NAV per share break"
        cases = group_into_cases(nav_breaks, "MERID-GEF", NAV_DATE)
        assert cases == [], "NAV-per-share differences are the control total, not investigable work"
