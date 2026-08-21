"""Memory changing a decision, which is the only thing that makes it memory.

`recurrence_key` has been computed on every case in this system since early in the build, carried
through `CaseFacts` and stamped onto spans, and **nothing read it**. A store that records history
nobody consults is a database. These tests exist to keep the difference real: the same error, on its
fourth occurrence, must reach a different outcome than on its first, and the reason must be
checkable.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from nav_sentinel.control_plane.repository import InMemoryRepository
from nav_sentinel.memory import recurrence
from nav_sentinel.remediation_office.materiality import (
    ISOLATED_THRESHOLD_BPS,
    RECURRENCE_TRIGGER,
    RECURRING_THRESHOLD_BPS,
    assess,
)

FUND = "MERID-GEF"
WINDOW = "2026-07-01"


@pytest.fixture
def store() -> InMemoryRepository:
    return InMemoryRepository()


def _record_error(store, case_id: str, as_of: str, fund: str = FUND) -> None:
    """A prior NAV error for this fund, as the runner would have persisted it."""
    store.save_case(
        case_id,
        {
            "case_id": case_id,
            "subject_id": fund,
            "as_of": as_of,
            "recurrence_key": recurrence.recurrence_key_for(fund),
        },
    )


class TestTheSameErrorIsAssessedDifferentlyOnARepeat:
    """The acceptance criterion, with both thresholds stated rather than implied."""

    #: Between the two thresholds on purpose: immaterial in isolation, material as a repeat. An
    #: error outside that band would pass this test without the recurrence logic doing anything.
    BETWEEN = (ISOLATED_THRESHOLD_BPS + RECURRING_THRESHOLD_BPS) / 2

    def test_the_chosen_error_actually_sits_between_the_thresholds(self):
        assert RECURRING_THRESHOLD_BPS < self.BETWEEN < ISOLATED_THRESHOLD_BPS

    def test_it_is_immaterial_on_a_first_occurrence(self):
        first = assess(
            error_bps=self.BETWEEN, affected_investors=41, prior_errors=0, since=WINDOW
        )
        assert not first.material
        assert first.basis == "isolated"
        assert first.threshold_bps == ISOLATED_THRESHOLD_BPS

    def test_it_is_material_once_the_fund_has_form(self):
        repeat = assess(
            error_bps=self.BETWEEN,
            affected_investors=41,
            prior_errors=RECURRENCE_TRIGGER + 1,
            since=WINDOW,
        )
        assert repeat.material
        assert repeat.basis == "recurring"
        assert repeat.threshold_bps == RECURRING_THRESHOLD_BPS

    def test_nothing_but_the_history_differs_between_the_two(self):
        """Guards against the outcome flipping for some other reason -- a different error size, a
        different population -- which would make this suite prove something it does not claim."""
        common = {"error_bps": self.BETWEEN, "affected_investors": 41, "since": WINDOW}
        first = assess(prior_errors=0, **common)
        repeat = assess(prior_errors=RECURRENCE_TRIGGER + 1, **common)
        assert first.error_bps == repeat.error_bps
        assert first.affected_investors == repeat.affected_investors
        assert first.material != repeat.material

    def test_the_trigger_is_a_boundary_not_a_slope(self):
        """One prior event is not a pattern. Asserted at the boundary because that is where an
        off-by-one in a governance threshold actually bites."""
        below = assess(
            error_bps=self.BETWEEN,
            affected_investors=1,
            prior_errors=RECURRENCE_TRIGGER - 1,
            since=WINDOW,
        )
        at = assess(
            error_bps=self.BETWEEN,
            affected_investors=1,
            prior_errors=RECURRENCE_TRIGGER,
            since=WINDOW,
        )
        assert below.basis == "isolated" and not below.material
        assert at.basis == "recurring" and at.material


class TestTheRationaleSaysWhichThresholdApplied:
    def test_it_names_the_threshold_and_the_history(self):
        a = assess(error_bps=D(30), affected_investors=41, prior_errors=3, since=WINDOW)
        assert "20bps threshold" in a.rationale
        assert "3 prior error" in a.rationale
        assert WINDOW in a.rationale

    def test_it_distinguishes_material_from_needing_compensation(self):
        """A material error nobody dealt on harms nobody. Collapsing the two would either pay
        nobody or overstate the remediation."""
        nobody = assess(error_bps=D(300), affected_investors=0, prior_errors=0, since=WINDOW)
        assert nobody.material
        assert not nobody.requires_compensation
        assert "nothing to compensate" in nobody.rationale


class TestBadInputsAreRefused:
    def test_a_signed_error_is_refused(self):
        """An overstatement of 285bps must not compare as smaller than an understatement of 20."""
        with pytest.raises(ValueError, match="magnitude"):
            assess(error_bps=D(-285), affected_investors=1, prior_errors=0, since=WINDOW)

    @pytest.mark.parametrize(("investors", "prior"), [(-1, 0), (1, -1)])
    def test_negative_counts_are_refused(self, investors, prior):
        with pytest.raises(ValueError):
            assess(
                error_bps=D(30), affected_investors=investors, prior_errors=prior, since=WINDOW
            )


class TestTheCountComesFromRecordedCases:
    """End to end: the number the assessment turns on is read from the store, not passed in."""

    def test_a_fund_with_no_history_has_no_prior_errors(self, store):
        found = recurrence.prior_errors(store, FUND, WINDOW)
        assert found["prior_errors"] == 0
        assert found["case_ids"] == []

    def test_prior_cases_for_the_same_fund_are_counted(self, store):
        for n, day in enumerate(("2026-07-14", "2026-07-28", "2026-08-11"), start=1):
            _record_error(store, f"CASE-REM-{n}", day)
        found = recurrence.prior_errors(store, FUND, WINDOW)
        assert found["prior_errors"] == 3
        assert len(found["case_ids"]) == 3

    def test_another_funds_errors_are_not_counted(self, store):
        _record_error(store, "CASE-REM-OTHER", "2026-07-14", fund="OTHER-FUND")
        assert recurrence.prior_errors(store, FUND, WINDOW)["prior_errors"] == 0

    def test_errors_before_the_window_are_not_counted(self, store):
        _record_error(store, "CASE-REM-OLD", "2026-06-30")
        _record_error(store, "CASE-REM-IN", "2026-07-01")
        found = recurrence.prior_errors(store, FUND, WINDOW)
        assert found["prior_errors"] == 1, "the window boundary is inclusive of `since`"
        assert found["case_ids"] == ["CASE-REM-IN"]

    def test_the_case_under_assessment_does_not_count_itself(self, store):
        """The off-by-one that would make every first error of a quarter look like a repeat."""
        _record_error(store, "CASE-REM-NOW", "2026-08-18")
        assert recurrence.prior_errors(store, FUND, WINDOW)["prior_errors"] == 1
        excluded = recurrence.prior_errors(store, FUND, WINDOW, excluding="CASE-REM-NOW")
        assert excluded["prior_errors"] == 0

    def test_the_count_carries_the_ids_behind_it(self, store):
        """A bare number is an unauditable claim about history."""
        _record_error(store, "CASE-REM-1", "2026-07-14")
        found = recurrence.prior_errors(store, FUND, WINDOW)
        assert found["case_ids"] == ["CASE-REM-1"]

    def test_a_malformed_window_is_refused(self, store):
        with pytest.raises(ValueError, match="ISO date"):
            recurrence.prior_errors(store, FUND, "last quarter")

    def test_the_recalled_count_drives_the_assessment(self, store):
        """The whole claim in one test: history in the store, decision out the other end."""
        error = TestTheSameErrorIsAssessedDifferentlyOnARepeat.BETWEEN

        clean = recurrence.prior_errors(store, FUND, WINDOW, excluding="CASE-REM-NOW")
        first = assess(
            error_bps=error,
            affected_investors=41,
            prior_errors=int(clean["prior_errors"]),
            since=str(clean["since"]),
        )
        assert not first.material

        for n, day in enumerate(("2026-07-14", "2026-07-28", "2026-08-11"), start=1):
            _record_error(store, f"CASE-REM-{n}", day)
        _record_error(store, "CASE-REM-NOW", "2026-08-18")

        withform = recurrence.prior_errors(store, FUND, WINDOW, excluding="CASE-REM-NOW")
        repeat = assess(
            error_bps=error,
            affected_investors=41,
            prior_errors=int(withform["prior_errors"]),
            since=str(withform["since"]),
        )
        assert repeat.material, "the same error, and the store is the only thing that changed"
        assert repeat.basis == "recurring"


class TestTheProjectionCarriesWhatP007Requires:
    def test_the_declared_facts_are_what_observe_returns(self):
        """A requirement over a fact the projection never emits could never be satisfied."""
        from nav_sentinel.control_plane import gateway

        required = set(gateway.evidence_requirement_for("rem.materiality"))
        projected = set(
            recurrence.observe(
                {"prior_errors": 3, "since": WINDOW}, {"fund_id": FUND}
            )
        )
        assert required <= projected, required - projected

    def test_a_count_without_its_window_is_not_enough(self):
        """`prior_errors` alone is uncheckable, exactly as a rate without its date is."""
        from nav_sentinel.control_plane import gateway

        assert "since" in gateway.evidence_requirement_for("rem.materiality")
