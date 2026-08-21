"""The multi-week case: one remediation, four departments, twenty-eight days, seven deliveries.

The claim these tests exist to make checkable is narrow and specific: **the case's position lives in
the store and nowhere else.** If any of it were carried in memory between events, "multi-week" would
describe a variable that happened to stay in scope rather than a property of the system.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from nav_sentinel import remediation_runner as runner
from nav_sentinel.control_plane import casefile, gateway
from nav_sentinel.control_plane.governance import IllegalTransition
from nav_sentinel.control_plane.repository import InMemoryRepository
from nav_sentinel.remediation_office import events
from nav_sentinel.remediation_office.lifecycle import AWAITING, REMEDIATION

TIMELINE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "data" / "remediation_timeline.json")
    .read_text()
)
CASE = TIMELINE["case_id"]


@pytest.fixture
def store() -> InMemoryRepository:
    return InMemoryRepository()


def _event(name: str, **extra) -> dict:
    return {"case_id": CASE, "event": name, **extra}


class TestTheTimelineIsInternallyConsistent:
    """A fixture whose own arithmetic disagrees with itself cannot support a claim about dates. The
    first draft of this file put approval and payment instruction on Sundays and had three day
    numbers that did not match their dates."""

    def test_every_day_number_matches_its_date(self):
        first = date.fromisoformat(TIMELINE["events"][0]["occurred_on"])
        for entry in TIMELINE["events"]:
            offset = (date.fromisoformat(entry["occurred_on"]) - first).days
            assert offset == entry["day"], entry["event"]

    def test_no_event_falls_on_a_weekend(self):
        for entry in TIMELINE["events"]:
            when = date.fromisoformat(entry["occurred_on"])
            assert when.weekday() < 5, f"{entry['event']} falls on a {when.strftime('%A')}"

    def test_the_span_is_genuinely_weeks(self):
        first = date.fromisoformat(TIMELINE["events"][0]["occurred_on"])
        last = date.fromisoformat(TIMELINE["events"][-1]["occurred_on"])
        assert (last - first).days >= 21, "a 'multi-week' timeline that is not weeks is a claim"

    def test_more_than_one_department_is_involved(self):
        departments = {e["department"] for e in TIMELINE["events"]}
        assert len(departments) >= 3, departments

    def test_every_event_in_the_timeline_is_one_the_process_defines(self):
        for entry in TIMELINE["events"]:
            assert entry["event"] in events.EVENT_STAGES, entry["event"]


class TestTheVocabularyAndTheLifecycleAgree:
    def test_every_event_names_a_declared_stage(self):
        outside = set(events.EVENT_STAGES.values()) - set(REMEDIATION.stages)
        assert not outside, outside

    def test_every_stage_is_reachable_by_some_event(self):
        """A stage no event can reach is a stage a case can never enter."""
        unreachable = set(REMEDIATION.stages) - set(events.EVENT_STAGES.values())
        assert not unreachable, unreachable

    def test_every_stage_says_what_it_is_waiting_for(self):
        assert set(AWAITING) == set(REMEDIATION.stages)

    def test_an_unknown_event_is_refused_rather_than_ignored(self):
        with pytest.raises(events.UnknownEvent):
            events.stage_for("vendor_onboarded")


class TestOneCaseWalksTheWholeTimeline:
    def test_the_recorded_timeline_closes_the_case(self, store):
        for entry in TIMELINE["events"]:
            applied = runner.apply_event(store, _event(entry["event"], note=entry["note"]))
        assert applied.closed
        assert applied.stage == "closed"

    def test_the_history_records_one_entry_per_delivered_event(self, store):
        for entry in TIMELINE["events"]:
            runner.apply_event(store, _event(entry["event"]))
        assert len(store.stages_for(CASE)) == len(TIMELINE["events"])

    def test_every_transition_left_a_policy_decision(self, store):
        gateway.mark_decisions("timeline")
        for entry in TIMELINE["events"]:
            runner.apply_event(store, _event(entry["event"]))
        stage_decisions = [
            d
            for d in gateway.decisions_since("timeline")
            if d.policy_id == "P-008-STAGE-TRANSITION"
        ]
        assert len(stage_decisions) == len(TIMELINE["events"])

    def test_a_parked_case_says_what_it_is_waiting_for(self, store):
        runner.apply_event(store, _event("error_detected"))
        applied = runner.apply_event(store, _event("impact_reported"))
        assert applied.awaiting == AWAITING["impact_assessed"]
        assert "materiality" in applied.awaiting


class TestStateLivesInTheStoreAndNowhereElse:
    """The load-bearing claim. Each test below hands the next event *nothing* but the store and the
    case id, which is all a cold instance handling a redelivery three weeks later actually has."""

    def test_a_second_process_can_continue_a_case_it_never_opened(self, store):
        runner.apply_event(store, _event("error_detected"))

        # Simulate the restart: nothing from the first call survives except the store itself. No
        # casefile object, no cached stage, no module-level state.
        del_ok = casefile.load(store, CASE)
        assert del_ok is not None and del_ok.stage == "detected"

        applied = runner.apply_event(store, _event("impact_reported"))
        assert applied.stage == "impact_assessed"

    def test_the_whole_timeline_survives_a_restart_between_every_event(self, store):
        """Seven deliveries, and between each one the only thing carried forward is the store.

        This is the substantive answer to "you replayed seven files in ninety seconds". The wall
        clock is compressed; the dependency on persisted state is not simulated.
        """
        stages: list[str] = []
        for entry in TIMELINE["events"]:
            # A fresh view of the case each time, derived from storage only.
            before = casefile.load(store, CASE)
            applied = runner.apply_event(store, _event(entry["event"]))
            stages.append(applied.stage)
            if before is not None:
                assert applied.stage != before.stage or not applied.advanced
        assert stages[-1] == "closed"
        assert stages == [
            "detected",
            "impact_assessed",
            "materiality_determined",
            "awaiting_approval",
            "approved",
            "compensation_in_flight",
            "closed",
        ]

    def test_an_event_for_a_case_that_was_never_opened_is_refused(self, store):
        with pytest.raises(runner.UnknownCase):
            runner.apply_event(store, _event("impact_reported"))

    def test_a_case_with_no_id_is_refused(self, store):
        with pytest.raises(runner.UnknownCase):
            runner.apply_event(store, {"event": "error_detected"})


class TestTheMachineRefusesWhatItMust:
    def test_compensation_before_approval_is_refused(self, store):
        """The transition the lifecycle deliberately omits. A well-formed payment event arriving
        before anyone signed must not move the case."""
        runner.apply_event(store, _event("error_detected"))
        runner.apply_event(store, _event("impact_reported"))
        runner.apply_event(store, _event("materiality_decided"))
        runner.apply_event(store, _event("routed_for_approval"))

        with pytest.raises(IllegalTransition):
            runner.apply_event(store, _event("compensation_started"))
        assert casefile.load(store, CASE).stage == "awaiting_approval"

    def test_the_refusal_is_recorded_as_a_denial(self, store):
        runner.apply_event(store, _event("error_detected"))
        gateway.mark_decisions("denial")
        with pytest.raises(IllegalTransition):
            runner.apply_event(store, _event("approval_recorded"))
        denials = [
            d
            for d in gateway.decisions_since("denial")
            if d.policy_id == "P-008-STAGE-TRANSITION" and d.effect.value == "deny"
        ]
        assert len(denials) == 1

    def test_an_immaterial_error_closes_without_compensation(self, store):
        runner.apply_event(store, _event("error_detected"))
        runner.apply_event(store, _event("impact_reported"))
        runner.apply_event(store, _event("materiality_decided"))
        applied = runner.apply_event(store, _event("closed_immaterial"))
        assert applied.closed
        assert [e["to"] for e in store.stages_for(CASE)] == [
            "detected",
            "impact_assessed",
            "materiality_determined",
            "closed",
        ]

    def test_an_unknown_event_is_permanently_undeliverable(self, store):
        runner.apply_event(store, _event("error_detected"))
        with pytest.raises(events.UnknownEvent) as refused:
            runner.apply_event(store, _event("vendor_onboarded"))
        assert isinstance(refused.value, runner.PERMANENT)


class TestAtLeastOnceDeliveryIsHandled:
    """Pub/Sub redelivers. A duplicate must be a no-op that reports success, or the subscription
    retries forever and the dead-letter topic fills with events that were in fact handled."""

    def test_a_duplicate_advance_is_a_no_op_not_an_error(self, store):
        runner.apply_event(store, _event("error_detected"))
        first = runner.apply_event(store, _event("impact_reported"))
        again = runner.apply_event(store, _event("impact_reported"))
        assert first.advanced and not again.advanced
        assert again.stage == "impact_assessed"
        assert len(store.stages_for(CASE)) == 2

    def test_a_redelivered_opening_event_does_not_reset_a_case(self, store):
        """The worst available outcome: resetting a case that is weeks into compensation."""
        for name in ("error_detected", "impact_reported", "materiality_decided"):
            runner.apply_event(store, _event(name))
        again = runner.apply_event(store, _event("error_detected"))
        assert not again.advanced
        assert again.stage == "materiality_determined"
        assert len(store.stages_for(CASE)) == 3


class TestTheThirdProcessIsStillAProcess:
    """It coordinates the other two, which is exactly why its isolation matters more than theirs.

    A supervisor that reaches into fund accounting and transfer agency would make the "coordination
    through the platform" claim false while leaving every other test green -- the two would be
    coupled through a third package instead of directly, which is worse because it is harder to see.
    """

    ROOT = Path(__file__).resolve().parents[1] / "src" / "nav_sentinel" / "remediation_office"

    #: Every other process-side package. A process may not import another process, and `agents` is
    #: a shared process-side layer reached only from the composition root.
    FORBIDDEN = (
        "nav_sentinel.domain",
        "nav_sentinel.tools",
        "nav_sentinel.transfer_agency",
        "nav_sentinel.agents",
        "nav_sentinel.pipeline",
        "nav_sentinel.evaluation",
        "nav_sentinel.memory",
    )

    #: What the platform publishes to a process. Not internals: `casefile` and `repository` are
    #: deliberately absent, which is why the lifecycle is *declared* here and walked by the
    #: composition root in `remediation_runner`.
    ALLOWED_PLATFORM = {
        "nav_sentinel.control_plane.packs",
        "nav_sentinel.control_plane.governance",
        "nav_sentinel.control_plane.gateway",
    }

    def _imports(self):
        import ast

        for path in self.ROOT.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and node.module:
                    yield path.name, node.module

    def test_it_imports_no_other_process(self):
        offenders = [
            (name, module)
            for name, module in self._imports()
            if module.startswith(self.FORBIDDEN)
        ]
        assert not offenders, offenders

    def test_it_reaches_the_platform_only_through_the_published_interface(self):
        for name, module in self._imports():
            if module.startswith("nav_sentinel.control_plane"):
                assert module in self.ALLOWED_PLATFORM, f"{name} imports {module}"

    def test_the_walking_happens_outside_the_pack(self):
        """The division this package's isolation depends on: it declares the lifecycle, and the
        composition root -- entitled to know about both layers -- walks it."""
        import ast

        runner_source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "nav_sentinel"
            / "remediation_runner.py"
        ).read_text()
        modules = {
            node.module
            for node in ast.walk(ast.parse(runner_source))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "nav_sentinel.control_plane.casefile" in modules
        assert "nav_sentinel.control_plane.repository" in modules
        assert any(m.startswith("nav_sentinel.remediation_office") for m in modules)


class TestTheWalkthroughRunsAndRenders:
    """`make ta` once died on an AttributeError with 631 tests green because nothing rendered its
    output. The same hole, closed before it opens: this drives the whole walkthrough offline."""

    #: A distinct case id per test. The store is configured once for the session and stage history
    #: is append-only, so two tests walking the same id would have the second one refused -- which
    #: is the machine behaving correctly, not a test to work around.
    @staticmethod
    def _run(monkeypatch, case_id: str) -> str:
        import sys

        from nav_sentinel import remediation_cli

        monkeypatch.setattr(
            sys, "argv", ["remediation_cli", "--offline", "--case-id", case_id]
        )
        remediation_cli.main()
        return case_id

    def test_the_offline_walkthrough_completes(self, monkeypatch):
        self._run(monkeypatch, "CASE-REM-WALK-0")

    def test_it_walks_every_stage_and_closes(self, monkeypatch):
        from nav_sentinel import composition
        from nav_sentinel.control_plane import casefile as cf

        case_id = self._run(monkeypatch, "CASE-REM-WALK-1")
        recovered = cf.load(composition.store(), case_id)
        assert recovered is not None
        assert recovered.stage == "closed"
        assert [e["to"] for e in recovered.history] == [
            "detected",
            "impact_assessed",
            "materiality_determined",
            "awaiting_approval",
            "approved",
            "compensation_in_flight",
            "closed",
        ]

    def test_every_stage_records_both_dates(self, monkeypatch):
        """The compression claim depends on both being present: when it happened, and when this
        system wrote it down."""
        from nav_sentinel import composition

        case_id = self._run(monkeypatch, "CASE-REM-WALK-2")
        history = composition.store().stages_for(case_id)
        assert all(entry["occurred_on"] for entry in history), history
        assert all(entry["recorded_at"] for entry in history)
        span = (
            date.fromisoformat(history[-1]["occurred_on"])
            - date.fromisoformat(history[0]["occurred_on"])
        ).days
        assert span >= 21, f"the business dates span only {span} days"

    def test_the_affected_population_comes_from_the_register(self, monkeypatch):
        """Not from the fixture. A count written into the timeline could disagree with the data,
        and did: it said 41 investors while the register held four."""
        from datetime import date as _date

        from nav_sentinel.transfer_agency import register

        for entry in TIMELINE["events"]:
            assert "affected_investors" not in entry, entry["event"]
        dealt = register.dealt_on(TIMELINE["fund_id"], _date.fromisoformat(TIMELINE["nav_date"]))
        assert dealt["holders"] > 0, "nobody dealt at the misstated price, so there is no case"

    def test_somebody_dealt_on_the_misstated_valuation_point(self):
        """The fixture coherence check the first live run failed. The register's only deal was
        dated three days before the NAV in question, so the impact report was 0 holders."""
        from datetime import date as _date

        from nav_sentinel.transfer_agency import register

        nav_date = _date.fromisoformat(TIMELINE["nav_date"])
        assert register.dealt_on(TIMELINE["fund_id"], nav_date)["deals"] > 0

    def test_the_error_size_sits_between_the_two_thresholds(self):
        """Otherwise the walkthrough would reach the same outcome with or without the recurrence
        count, and the beat it exists to show would be decoration."""
        from decimal import Decimal

        from nav_sentinel.remediation_office import materiality

        error = Decimal(TIMELINE["error_bps"])
        assert materiality.RECURRING_THRESHOLD_BPS < error < materiality.ISOLATED_THRESHOLD_BPS

    def test_the_seeded_history_crosses_the_recurrence_trigger(self):
        from nav_sentinel.remediation_office import materiality

        assert len(TIMELINE["prior_errors"]) >= materiality.RECURRENCE_TRIGGER

    def test_a_closed_case_refuses_replay_with_a_message_not_a_traceback(self, monkeypatch, capsys):
        """Append-only history means a second run cannot reopen a case. That is correct; crashing
        on it is not. Found by two tests sharing a store, which is exactly the situation a second
        `make remediation` against Firestore creates."""
        self._run(monkeypatch, "CASE-REM-REPLAY")
        capsys.readouterr()
        self._run(monkeypatch, "CASE-REM-REPLAY")
        out = capsys.readouterr().out
        assert "already exists at stage" in out
        assert "closed" in out
        assert "--case-id" in out, "the refusal should say how to run another one"

    def test_replaying_appends_nothing(self, monkeypatch):
        from nav_sentinel import composition

        case_id = self._run(monkeypatch, "CASE-REM-REPLAY-2")
        before = composition.store().stages_for(case_id)
        self._run(monkeypatch, case_id)
        assert composition.store().stages_for(case_id) == before
