"""Invariants of the deterministic reconciliation core."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from nav_sentinel.domain import tolerance
from nav_sentinel.domain.cycle import group_into_cases, signed_impact_base
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
        local_price=Decimal(1), local_currency=ccy, fx_rate=Decimal(1),
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

        breaks = tolerance.detect_position_breaks(accounting, custodian, NAV_DATE)
        qty = next(b for b in breaks if b.break_type == BreakType.POSITION_QUANTITY)

        assert qty.accounting_value == Decimal(150), "lots must aggregate, not overwrite"
        assert qty.difference == Decimal(50)

    def test_no_break_when_lots_sum_to_custodian(self):
        accounting = [
            _pos("F1", "ISIN1", "60", "600", "accounting"),
            _pos("F1", "ISIN1", "40", "400", "accounting"),
        ]
        custodian = [_pos("F1", "ISIN1", "100", "1000", "custodian")]
        assert tolerance.detect_position_breaks(accounting, custodian, NAV_DATE) == []


class TestMaterialityUnits:
    """Quantity, market value, cash and NAV-per-share are different units and must never
    be summed together."""

    def test_quantity_break_carries_no_value(self):
        breaks = tolerance.detect_position_breaks(
            [_pos("F1", "ISIN1", "150", "1000", "accounting")],
            [_pos("F1", "ISIN1", "100", "1000", "custodian")],
            NAV_DATE,
        )
        case = group_into_cases(breaks, "F1", NAV_DATE)[0]
        assert case.quantity_breaks, "expected a quantity break"
        assert case.value_breaks == [], "a quantity difference carries no monetary value"
        assert signed_impact_base(case) == Decimal(0)

    def test_split_has_quantity_break_but_zero_nav_impact(self):
        """A 2:1 split unapplied on one side: quantity differs 2x, value agrees."""
        breaks = tolerance.detect_position_breaks(
            bnr.positions("accounting"), bnr.positions("custodian"), NAV_DATE
        )
        split = [b for b in breaks if b.isin == "US5949181045"]
        assert len(split) == 1
        assert split[0].break_type == BreakType.POSITION_QUANTITY
        assert split[0].abs_difference == Decimal("96000.0000")

        case = group_into_cases(split, "MERID-GEF", NAV_DATE)[0]
        nav = bnr.nav_record("custodian", "MERID-GEF", NAV_DATE)
        score(case, nav, make_to_base("EUR", NAV_DATE))
        assert case.nav_impact_bps == 0.0, "a 2:1 split moves no value"
        # NOT auto-clear. A 2x stock-record break drives wrong dividend entitlement and wrong
        # future valuation whatever today's monetary impact is, and no administrator clears one.
        # The previous version asserted AUTO_CLEAR, pinning the wrong behaviour as correct.
        assert case.approval_class is not ApprovalClass.AUTO_CLEAR, (
            "a quantity break must not auto-clear on zero monetary impact"
        )


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


class TestGoldenClosesTheControlTotal:
    """B2. The declared ground truth must account for the whole NAV difference.

    It previously explained 2.4% of it, because two trade-date recognitions were booked without
    their contra cash leg. These tests are the ones the project's own thesis implies and which
    nothing asserted.
    """

    @pytest.fixture(scope="class")
    @staticmethod
    def golden():
        import yaml

        path = Path(__file__).resolve().parents[1] / "eval" / "golden_breaks.yaml"
        return yaml.safe_load(path.read_text())

    def test_every_cycle_declares_its_control_total(self, golden):
        assert golden["cycles"], "no cycles in the golden file"
        for cycle in golden["cycles"]:
            assert Decimal(cycle["control_total"]) is not None
            assert cycle["scenarios"], f"{cycle['nav_date']} declares no scenarios"

    @pytest.mark.parametrize("cycle_index", [0, 1])
    def test_posting_the_declared_corrections_reconciles_the_books(self, golden, cycle_index):
        """Post every declared correction onto the accounting side and the residual is zero.

        Not "do the translated corrections sum to minus the control total" -- that compares two
        differently-rounded quantities and fails by a cent on figures that are otherwise right.
        """
        cycle = golden["cycles"][cycle_index]
        as_of = date.fromisoformat(cycle["nav_date"])
        base = golden["base_currency"]
        to_base = make_to_base(base, as_of)

        acc = bnr.nav_record("accounting", golden["fund_id"], as_of)
        cus = bnr.nav_record("custodian", golden["fund_id"], as_of)
        control_total = acc.net_assets - cus.net_assets
        assert control_total == Decimal(cycle["control_total"])

        posted = Decimal(0)
        for scenario in cycle["scenarios"]:
            for leg in scenario["expected_corrections"]:
                if leg["leg"] == "quantity_restatement":
                    continue
                amount = Decimal(leg["amount"])
                ccy = leg["currency"] or base
                posted += amount if ccy == base else to_base(amount, ccy)

        # Two cents, deliberately, and not to be tightened. Translating a foreign-currency
        # correction rounds once and the balance it affects rounds once, and
        # money(a/r) - money(b/r) is not money((a-b)/r). The generator's own assertion avoids the
        # double rounding by *posting* the corrections and recomputing, which closes exactly; this
        # is the weaker summed form, quoted here because it is the one a reader can check by hand.
        residual = control_total + posted
        assert abs(residual) <= Decimal("0.02"), (
            f"{as_of}: control total {control_total}, corrections {posted}, residual {residual}. "
            f"A non-zero residual means a scenario moves net assets by an amount its own "
            f"correction does not account for."
        )

    def test_withholding_one_scenario_leaves_exactly_its_impact(self, golden):
        """The negative test. Without it the closure assertion has never been shown to fail, and
        a control that cannot fail is not a control."""
        cycle = golden["cycles"][1]
        as_of = date.fromisoformat(cycle["nav_date"])
        base = golden["base_currency"]
        to_base = make_to_base(base, as_of)

        def total(scenarios):
            out = Decimal(0)
            for scenario in scenarios:
                for leg in scenario["expected_corrections"]:
                    if leg["leg"] == "quantity_restatement":
                        continue
                    amount, ccy = Decimal(leg["amount"]), (leg["currency"] or base)
                    out += amount if ccy == base else to_base(amount, ccy)
            return out

        monetary = [
            s for s in cycle["scenarios"]
            if any(leg["leg"] != "quantity_restatement" for leg in s["expected_corrections"])
            and total([s]) != 0
        ]
        assert monetary, "expected at least one scenario with a monetary correction"

        full = total(cycle["scenarios"])
        for held_out in monetary:
            remaining = total([s for s in cycle["scenarios"] if s is not held_out])
            missing = full - remaining
            assert missing.quantize(Decimal("0.01")) == total([held_out]).quantize(
                Decimal("0.01")
            ), (
                f"withholding {held_out['scenario']} should leave exactly its own impact"
            )
            assert missing != 0


class TestStoredValuesAreDerivable:
    """B4. Corrupting every `fx_rate` in the accounting book left the whole suite green, because
    `market_value_base` is a stored field nothing recomputed. The FX chain the investigator exists
    to check was untested."""

    @pytest.mark.parametrize("source", ["accounting", "custodian"])
    def test_market_value_equals_quantity_times_price_over_rate(self, source):
        for p in bnr.positions(source):
            expected = (p.quantity * p.local_price / p.fx_rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            assert p.market_value_base == expected, (
                f"{source} {p.isin} @ {p.as_of}: stored {p.market_value_base}, "
                f"{p.quantity} x {p.local_price} / {p.fx_rate} = {expected}"
            )

    def test_base_currency_positions_carry_a_unit_rate(self):
        for source in ("accounting", "custodian"):
            for p in bnr.positions(source):
                if p.local_currency == "EUR":
                    assert p.fx_rate == Decimal(1), f"{p.isin} in base carries {p.fx_rate}"

    @pytest.mark.live
    def test_rates_match_the_ecb_for_their_stated_date(self):
        """The custodian book is valued at the NAV date throughout, so every non-base rate in it
        must be the published rate for that date. The accounting book deliberately deviates --
        that is scenario 1 -- so it is excluded."""
        from nav_sentinel.tools import ecb_fx

        for p in bnr.positions("custodian"):
            if p.local_currency == "EUR" or p.as_of != NAV_DATE:
                continue
            local = ecb_fx.latest_rate_on_or_before(p.local_currency, p.as_of)
            base = ecb_fx.latest_rate_on_or_before("EUR", p.as_of)
            expected = (local[1] / base[1]).quantize(Decimal("0.00000001"))
            assert p.fx_rate == expected, f"{p.isin}: stored {p.fx_rate}, ECB {expected}"


class TestControlTotalIsNotWork:
    def test_nav_per_share_break_is_control_total_not_work(self):
        nav_breaks = tolerance.detect_nav_breaks(
            bnr.nav_records("accounting"), bnr.nav_records("custodian"), NAV_DATE
        )
        assert nav_breaks, "expected a NAV per share break"
        cases = group_into_cases(nav_breaks, "MERID-GEF", NAV_DATE)
        assert cases == [], "NAV-per-share differences are the control total, not investigable work"
