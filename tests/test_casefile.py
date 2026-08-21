"""The stage machine: a case that outlives the request that opened it.

`ExceptionStatus` has carried ten values including `AWAITING_APPROVAL` since early in this build and
nothing ever transitioned between them -- the enum described an intention. These tests exist to keep
that from being true of the replacement, so each one names a state and then produces it.
"""

from __future__ import annotations

import pytest

from nav_sentinel.control_plane import casefile, gateway
from nav_sentinel.control_plane.casefile import IllegalTransition, Lifecycle, UnknownStage
from nav_sentinel.control_plane.repository import ImmutableRecord, InMemoryRepository

#: A lifecycle with a branch in it, because a linear one would not exercise the graph. An immaterial
#: error is closed without compensation; a material one goes the long way round.
REMEDIATION = Lifecycle(
    stages=(
        "detected",
        "impact_assessed",
        "materiality_determined",
        "awaiting_approval",
        "approved",
        "compensation_in_flight",
        "closed",
    ),
    transitions=(
        ("detected", "impact_assessed"),
        ("impact_assessed", "materiality_determined"),
        ("materiality_determined", "awaiting_approval"),
        ("materiality_determined", "closed"),
        ("awaiting_approval", "approved"),
        ("approved", "compensation_in_flight"),
        ("compensation_in_flight", "closed"),
    ),
    initial="detected",
    terminal=("closed",),
)

CASE = "CASE-REM-MERID-GEF-2026-08-17"


@pytest.fixture
def store() -> InMemoryRepository:
    return InMemoryRepository()


class TestTheGraphIsCheckedWhenItIsDeclared:
    """A graph mistake should be refused at construction, not discovered by a stuck case."""

    def test_a_transition_naming_an_undeclared_stage_is_refused(self):
        with pytest.raises(UnknownStage):
            Lifecycle(
                stages=("a", "b"),
                transitions=(("a", "z"),),
                initial="a",
                terminal=("b",),
            )

    def test_a_terminal_stage_with_an_outbound_edge_is_refused(self):
        """It is not terminal, and calling it terminal is how a case gets closed and then moves."""
        with pytest.raises(IllegalTransition):
            Lifecycle(
                stages=("a", "b"),
                transitions=(("a", "b"), ("b", "a")),
                initial="a",
                terminal=("b",),
            )

    def test_a_non_terminal_stage_with_no_way_out_is_refused(self):
        """A case reaching it can never progress or close, which is a silent stall."""
        with pytest.raises(IllegalTransition):
            Lifecycle(
                stages=("a", "b", "c"),
                transitions=(("a", "b"),),
                initial="a",
                terminal=("c",),
            )

    def test_an_undeclared_initial_stage_is_refused(self):
        with pytest.raises(UnknownStage):
            Lifecycle(stages=("a",), transitions=(), initial="z", terminal=("a",))


class TestACaseAdvancesAndRefuses:
    def test_a_case_opens_at_its_declared_initial_stage(self, store):
        opened = casefile.open_case(store, CASE, REMEDIATION)
        assert opened.stage == "detected"
        assert opened.sequence == 0

    def test_a_declared_transition_advances_and_appends(self, store):
        opened = casefile.open_case(store, CASE, REMEDIATION)
        moved = casefile.advance(store, opened, "impact_assessed", REMEDIATION)
        assert moved.stage == "impact_assessed"
        assert moved.sequence == 1
        assert [e["to"] for e in store.stages_for(CASE)] == ["detected", "impact_assessed"]

    def test_compensation_before_approval_is_refused(self, store):
        """The transition this machine exists for. It is individually plausible, it arrives as a
        well-formed external event, and it must not happen."""
        opened = casefile.open_case(store, CASE, REMEDIATION)
        assessed = casefile.advance(store, opened, "impact_assessed", REMEDIATION)
        determined = casefile.advance(store, assessed, "materiality_determined", REMEDIATION)

        with pytest.raises(IllegalTransition) as refused:
            casefile.advance(store, determined, "compensation_in_flight", REMEDIATION)
        assert "not a declared transition" in str(refused.value)
        # And the case did not move.
        assert casefile.load(store, CASE).stage == "materiality_determined"

    def test_a_refusal_is_recorded_as_a_policy_decision(self, store):
        """A rejected event that left no trace is indistinguishable from one that never arrived."""
        opened = casefile.open_case(store, CASE, REMEDIATION)
        gateway.mark_decisions("refusal")
        with pytest.raises(IllegalTransition):
            casefile.advance(store, opened, "closed", REMEDIATION)

        denials = [
            d
            for d in gateway.decisions_since("refusal")
            if d.policy_id == "P-008-STAGE-TRANSITION" and d.effect.value == "deny"
        ]
        assert len(denials) == 1
        assert denials[0].resource == CASE
        assert denials[0].metadata["to"] == "closed"

    def test_an_undeclared_stage_is_refused_and_recorded(self, store):
        opened = casefile.open_case(store, CASE, REMEDIATION)
        gateway.mark_decisions("unknown")
        with pytest.raises(UnknownStage):
            casefile.advance(store, opened, "sent_to_the_regulator", REMEDIATION)
        assert any(
            d.policy_id == "P-008-STAGE-TRANSITION" and d.effect.value == "deny"
            for d in gateway.decisions_since("unknown")
        )

    def test_every_allowed_transition_is_also_recorded(self, store):
        gateway.mark_decisions("allowed")
        opened = casefile.open_case(store, CASE, REMEDIATION)
        casefile.advance(store, opened, "impact_assessed", REMEDIATION)
        allowed = [
            d
            for d in gateway.decisions_since("allowed")
            if d.policy_id == "P-008-STAGE-TRANSITION" and d.effect.value == "allow"
        ]
        assert len(allowed) == 2, "opening and advancing are both governance events"

    def test_the_branch_is_usable(self, store):
        """An immaterial error closes without compensation. A linear lifecycle could not say that."""
        opened = casefile.open_case(store, CASE, REMEDIATION)
        assessed = casefile.advance(store, opened, "impact_assessed", REMEDIATION)
        determined = casefile.advance(store, assessed, "materiality_determined", REMEDIATION)
        closed = casefile.advance(store, determined, "closed", REMEDIATION)
        assert closed.stage == "closed"


class TestStateSurvivesTheProcessThatMadeIt:
    """The claim the whole section rests on. If this is a variable that happened to still be in
    scope, "multi-week" is a word rather than a property."""

    def test_a_casefile_is_rebuilt_from_history_by_a_caller_that_never_saw_it(self, store):
        opened = casefile.open_case(store, CASE, REMEDIATION)
        casefile.advance(store, opened, "impact_assessed", REMEDIATION)

        # No reference to anything the writer held: only the store and the id, which is what a
        # second Pub/Sub delivery on a cold instance actually has.
        recovered = casefile.load(store, CASE)
        assert recovered.stage == "impact_assessed"
        assert recovered.sequence == 1
        assert len(recovered.history) == 2

    def test_an_unknown_case_loads_as_nothing_rather_than_an_empty_case(self, store):
        assert casefile.load(store, "CASE-NEVER-OPENED") is None

    def test_the_stage_is_derived_from_history_and_cannot_drift_from_it(self, store):
        """Deliberately no `stage` field on the case document. A cached stage beside a history is
        the thing that silently disagrees with it."""
        opened = casefile.open_case(store, CASE, REMEDIATION)
        casefile.advance(store, opened, "impact_assessed", REMEDIATION)
        history = store.stages_for(CASE)
        assert casefile.load(store, CASE).stage == history[-1]["to"]


class TestAtLeastOnceDeliveryCannotDoubleAdvanceACase:
    """Pub/Sub is at-least-once. Two deliveries of one event must not move a case twice."""

    def test_a_repeated_transition_at_the_same_position_collides(self, store):
        opened = casefile.open_case(store, CASE, REMEDIATION)
        casefile.advance(store, opened, "impact_assessed", REMEDIATION)

        # The same stale casefile, as a redelivered event would carry.
        with pytest.raises(ImmutableRecord):
            casefile.advance(store, opened, "impact_assessed", REMEDIATION)

    def test_the_case_is_unmoved_after_a_collision(self, store):
        """The redelivered transition must be one that is *legal* from the stale stage, or the
        machine refuses it for the wrong reason and this proves nothing about idempotency."""
        opened = casefile.open_case(store, CASE, REMEDIATION)
        first = casefile.advance(store, opened, "impact_assessed", REMEDIATION)
        before = store.stages_for(CASE)

        with pytest.raises(ImmutableRecord):
            casefile.advance(store, opened, "impact_assessed", REMEDIATION)

        assert casefile.load(store, CASE).stage == first.stage
        assert store.stages_for(CASE) == before, "the collision appended something"

    def test_reopening_a_case_collides_rather_than_resetting_it(self, store):
        casefile.open_case(store, CASE, REMEDIATION)
        with pytest.raises(ImmutableRecord):
            casefile.open_case(store, CASE, REMEDIATION)


class TestHistoryCarriesWhatAReviewerNeeds:
    def test_each_entry_names_where_the_case_came_from_and_went(self, store):
        opened = casefile.open_case(store, CASE, REMEDIATION, note="published NAV misstated")
        casefile.advance(
            store,
            opened,
            "impact_assessed",
            REMEDIATION,
            note="41 investors dealt at the wrong price",
            evidence=("OBS-abc123",),
        )
        history = store.stages_for(CASE)
        assert history[0]["from"] is None
        assert history[1]["from"] == "detected"
        assert history[1]["evidence"] == ["OBS-abc123"]
        assert "41 investors" in history[1]["note"]

    def test_every_entry_carries_a_wall_clock_timestamp(self, store):
        """This is what makes the compressed-timeline claim demonstrable rather than asserted: the
        writes are visibly distinct in time even when the business dates are weeks apart."""
        opened = casefile.open_case(store, CASE, REMEDIATION)
        casefile.advance(store, opened, "impact_assessed", REMEDIATION)
        stamps = [e["recorded_at"] for e in store.stages_for(CASE)]
        assert all(s.endswith("+00:00") for s in stamps), stamps
        assert len(stamps) == 2
