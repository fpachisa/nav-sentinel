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
#: Events the lifecycle is expected to refuse. One is deliberately out of order -- a payment release
#: arriving before approval -- because real settlement systems fire early and the governance record
#: should show a refusal rather than only allows.
REFUSED = [e for e in TIMELINE["events"] if e.get("expect") == "refused"]
ADVANCING = [e for e in TIMELINE["events"] if e.get("expect") != "refused"]


@pytest.fixture
def store() -> InMemoryRepository:
    return InMemoryRepository()


def _event(name: str, **extra) -> dict:
    return {"case_id": CASE, "event": name, **extra}


def _facts(case_id: str = CASE):
    """The audit facts every delivered event carries.

    `apply_event` requires them: it opens a span and persists the decisions the event produced, and
    an optional audit record is one that is absent wherever a caller forgot.
    """
    from nav_sentinel.control_plane.governance import CaseFacts

    return CaseFacts(
        case_id=case_id,
        subject_id=TIMELINE["fund_id"],
        as_of=date.fromisoformat(TIMELINE["nav_date"]),
        capability="rem.materiality",
        status="open",
        item_count=1,
    )


def _apply(store, name: str, **extra):
    return runner.apply_event(store, _event(name, **extra), facts=_facts())


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
        for entry in ADVANCING:
            applied = _apply(store, entry["event"], note=entry["note"])
        assert applied.closed
        assert applied.stage == "closed"

    def test_the_history_records_one_entry_per_accepted_event(self, store):
        """Refused deliveries leave a decision, not a stage. Counting them here would assert that a
        refusal moved the case."""
        for entry in ADVANCING:
            _apply(store, entry["event"])
        assert len(store.stages_for(CASE)) == len(ADVANCING)

    def test_the_out_of_order_event_is_refused_and_the_case_does_not_move(self, store):
        """The demo's governance beat, and a real failure mode: a settlement system releasing a
        payment file before anyone approved."""
        assert REFUSED, "the timeline no longer contains a refused delivery"
        for entry in ADVANCING:
            if entry["event"] == "approval_recorded":
                break
            _apply(store, entry["event"])
        before = len(store.stages_for(CASE))
        for entry in REFUSED:
            with pytest.raises(IllegalTransition):
                _apply(store, entry["event"])
        assert len(store.stages_for(CASE)) == before
        denials = [
            d for d in store.decisions_for(CASE) if d.get("nav.policy.effect") == "deny"
        ]
        assert denials, "the refusal was not persisted"

    def test_every_transition_left_a_policy_decision(self, store):
        gateway.mark_decisions("timeline")
        for entry in ADVANCING:
            _apply(store, entry["event"])
        stage_decisions = [
            d
            for d in gateway.decisions_since("timeline")
            if d.policy_id == "P-008-STAGE-TRANSITION"
        ]
        assert len(stage_decisions) == len(ADVANCING)

    def test_a_parked_case_says_what_it_is_waiting_for(self, store):
        _apply(store, "error_detected")
        applied = _apply(store, "impact_reported")
        assert applied.awaiting == AWAITING["impact_assessed"]
        assert "materiality" in applied.awaiting


class TestStateLivesInTheStoreAndNowhereElse:
    """The load-bearing claim. Each test below hands the next event *nothing* but the store and the
    case id, which is all a cold instance handling a redelivery three weeks later actually has."""

    def test_a_second_process_can_continue_a_case_it_never_opened(self, store):
        _apply(store, "error_detected")

        # Simulate the restart: nothing from the first call survives except the store itself. No
        # casefile object, no cached stage, no module-level state.
        del_ok = casefile.load(store, CASE)
        assert del_ok is not None and del_ok.stage == "detected"

        applied = _apply(store, "impact_reported")
        assert applied.stage == "impact_assessed"

    def test_the_whole_timeline_survives_a_restart_between_every_event(self, store):
        """Seven deliveries, and between each one the only thing carried forward is the store.

        This is the substantive answer to "you replayed seven files in ninety seconds". The wall
        clock is compressed; the dependency on persisted state is not simulated.
        """
        stages: list[str] = []
        for entry in ADVANCING:
            # A fresh view of the case each time, derived from storage only.
            before = casefile.load(store, CASE)
            applied = _apply(store, entry["event"])
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
            _apply(store, "impact_reported")

    def test_a_case_with_no_id_is_refused(self, store):
        with pytest.raises(runner.UnknownCase):
            runner.apply_event(store, {"event": "error_detected"}, facts=_facts())


class TestTheMachineRefusesWhatItMust:
    def test_compensation_before_approval_is_refused(self, store):
        """The transition the lifecycle deliberately omits. A well-formed payment event arriving
        before anyone signed must not move the case."""
        _apply(store, "error_detected")
        _apply(store, "impact_reported")
        _apply(store, "materiality_decided")
        _apply(store, "routed_for_approval")

        with pytest.raises(IllegalTransition):
            _apply(store, "compensation_started")
        assert casefile.load(store, CASE).stage == "awaiting_approval"

    def test_the_refusal_is_recorded_as_a_denial(self, store):
        _apply(store, "error_detected")
        gateway.mark_decisions("denial")
        with pytest.raises(IllegalTransition):
            _apply(store, "approval_recorded")
        denials = [
            d
            for d in gateway.decisions_since("denial")
            if d.policy_id == "P-008-STAGE-TRANSITION" and d.effect.value == "deny"
        ]
        assert len(denials) == 1

    def test_an_immaterial_error_closes_without_compensation(self, store):
        _apply(store, "error_detected")
        _apply(store, "impact_reported")
        _apply(store, "materiality_decided")
        applied = _apply(store, "closed_immaterial")
        assert applied.closed
        assert [e["to"] for e in store.stages_for(CASE)] == [
            "detected",
            "impact_assessed",
            "materiality_determined",
            "closed",
        ]

    def test_an_unknown_event_is_permanently_undeliverable(self, store):
        _apply(store, "error_detected")
        with pytest.raises(events.UnknownEvent) as refused:
            _apply(store, "vendor_onboarded")
        assert isinstance(refused.value, runner.PERMANENT)


class TestAtLeastOnceDeliveryIsHandled:
    """Pub/Sub redelivers. A duplicate must be a no-op that reports success, or the subscription
    retries forever and the dead-letter topic fills with events that were in fact handled."""

    def test_a_duplicate_advance_is_a_no_op_not_an_error(self, store):
        _apply(store, "error_detected")
        first = _apply(store, "impact_reported")
        again = _apply(store, "impact_reported")
        assert first.advanced and not again.advanced
        assert again.stage == "impact_assessed"
        assert len(store.stages_for(CASE)) == 2

    def test_a_redelivered_opening_event_does_not_reset_a_case(self, store):
        """The worst available outcome: resetting a case that is weeks into compensation."""
        for name in ("error_detected", "impact_reported", "materiality_decided"):
            _apply(store, name)
        again = _apply(store, "error_detected")
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
        """Every module this package names, however it names it.

        `ast.ImportFrom` alone was not enough, and the gap was total: `import x.y as z` is an
        `ast.Import` and was never visited, while `from nav_sentinel import transfer_agency` has
        `node.module == "nav_sentinel"` and matched no forbidden prefix. Measured -- adding both
        lines to this package left all 755 tests green, so the seam claim rested on nothing.
        """
        import ast

        for path in self.ROOT.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        yield path.name, alias.name, "module"
                elif isinstance(node, ast.ImportFrom) and node.module:
                    yield path.name, node.module, "module"
                    # `from package import name` may reach the *module* `package.name`. Yielded
                    # separately: it is only a candidate module path, so a prefix check must see it
                    # while an exact allow-list must not reject `governance.CaseBrief` for not
                    # being a module.
                    for alias in node.names:
                        yield path.name, f"{node.module}.{alias.name}", "member"

    def test_it_imports_no_other_process(self):
        """Both forms and both kinds: a process package reached by any spelling is still reached."""
        offenders = [
            (name, module)
            for name, module, _kind in self._imports()
            if module.startswith(self.FORBIDDEN)
        ]
        assert not offenders, offenders

    def test_it_reaches_the_platform_only_through_the_published_interface(self):
        for name, module, kind in self._imports():
            if kind != "module":
                continue
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

    def test_a_finished_case_refuses_replay_with_a_message_not_a_traceback(self, monkeypatch, capsys):
        """A *finished* case has nowhere to go. Crashing on it is not the same as refusing it.

        Only terminal cases are refused. The first version refused every existing case and blamed
        append-only history for it, which was wrong twice -- and refusing to resume a parked case is
        refusing the thing this section is about.
        """
        self._run(monkeypatch, "CASE-REM-REPLAY")
        capsys.readouterr()
        self._run(monkeypatch, "CASE-REM-REPLAY")
        out = capsys.readouterr().out
        assert "is already" in out
        assert "closed" in out
        assert "--case-id" in out, "the refusal should say how to run another one"

    def test_a_parked_case_resumes_rather_than_being_refused(self, monkeypatch, capsys):
        """The behaviour the section exists to demonstrate: a later invocation picks the case up."""
        from nav_sentinel import composition
        from nav_sentinel.control_plane import casefile as cf

        case_id = "CASE-REM-RESUMED"
        for name in ("error_detected", "impact_reported"):
            runner.apply_event(
                composition.store(),
                {"case_id": case_id, "event": name, "occurred_on": "2026-08-18"},
                facts=_facts(case_id),
            )
        assert cf.load(composition.store(), case_id).stage == "impact_assessed"
        capsys.readouterr()

        self._run(monkeypatch, case_id)
        out = capsys.readouterr().out
        assert "resuming" in out
        assert "already recorded" in out
        assert cf.load(composition.store(), case_id).stage == "closed"

    def test_a_resumed_case_reads_its_population_back_rather_than_assuming_zero(
        self, monkeypatch, capsys
    ):
        """Skipping the impact event left the population unknown, and unknown fell through as zero
        -- which closes a material error with nothing paid. The case document holds it."""
        from nav_sentinel import composition

        case_id = "CASE-REM-POP"
        store = composition.store()
        self._run(monkeypatch, case_id)          # a full walk persists the population
        stored = store.load_case(case_id)
        assert stored and stored["affected_investors"] > 0, stored

        # A second case parked mid-way, with the population already on its document.
        parked = "CASE-REM-POP-2"
        for name in ("error_detected", "impact_reported"):
            runner.apply_event(
                store,
                {"case_id": parked, "event": name, "occurred_on": "2026-08-18"},
                facts=_facts(parked),
            )
        store.save_case(parked, {**stored, "case_id": parked})
        capsys.readouterr()
        self._run(monkeypatch, parked)
        out = capsys.readouterr().out
        assert "affected investors read back" in out
        assert "nothing to compensate" not in out

    def test_replaying_appends_nothing(self, monkeypatch):
        from nav_sentinel import composition

        case_id = self._run(monkeypatch, "CASE-REM-REPLAY-2")
        before = composition.store().stages_for(case_id)
        self._run(monkeypatch, case_id)
        assert composition.store().stages_for(case_id) == before


class TestTheOfficersAnswerIsCheckedNotDisplayed:
    """The defect this class exists for: the officer ran, its verdict was printed, and the
    assessment then read the count straight from the store -- so the model's answer changed nothing
    and the run would have been identical had it returned nonsense. A model call whose result is
    discarded is theatre. Found while writing the review brief, which is where several of this
    project's defects have been found."""

    @staticmethod
    def _dealing_verdict():
        """What transfer agency's reporter would answer: a holder count for the dealing date."""
        from datetime import UTC, datetime

        from nav_sentinel.agents.contract import Citation, Verdict
        from nav_sentinel.control_plane.observations import Observation, ObservationStore

        store = ObservationStore()
        observation = store.record(
            Observation(
                observation_id="OBS-dealing00000000",
                case_id="CASE-REM-DISPUTE",
                trace_id="d8bc651a64bdcd4eac21517327b02b85",
                agent_ref="dealing-impact-reporter@1.0.0",
                tool="register.dealt_on",
                args="fund_id=MERID-GEF,trade_date=2026-08-17",
                digest="0123456789abcdef",
                retrieved_at=datetime(2026, 8, 19, 9, 0, tzinfo=UTC),
                source="share_register",
                source_uri="register://merian/dealt_on/registrar",
                observed={"holders": "4", "units": "101250", "trade_date": "2026-08-17"},
                summary="4 holders dealt on 2026-08-17",
            )
        )
        return (
            Verdict(
                case_id="CASE-REM-DISPUTE",
                capability="ta.dealing_impact",
                root_cause="4 holders dealt 101250 units on 2026-08-17",
                confidence=0.95,
                citations=[
                    Citation(observation_id=observation.observation_id, relevance="the count")
                ],
            ),
            store,
        )

    @staticmethod
    def _verdict(count: int, *, cause: str = "3 prior errors since 2026-07-01"):
        from datetime import UTC, datetime

        from nav_sentinel.agents.contract import Citation, Verdict
        from nav_sentinel.control_plane.observations import Observation, ObservationStore

        store = ObservationStore()
        observation = store.record(
            Observation(
                observation_id="OBS-cited000000000",
                case_id="CASE-REM-CHECK",
                trace_id="d8bc651a64bdcd4eac21517327b02b85",
                agent_ref="remediation-officer@1.0.0",
                tool="memory.prior_errors",
                args="fund_id=MERID-GEF,since=2026-07-01",
                digest="0123456789abcdef",
                retrieved_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
                source="recorded_case_history",
                source_uri="memory://recurrence/MERID-GEF",
                observed={"prior_errors": str(count), "since": "2026-07-01"},
                summary=f"{count} prior errors",
            )
        )
        verdict = Verdict(
            case_id="CASE-REM-CHECK",
            capability="rem.materiality",
            root_cause=cause,
            confidence=0.95,
            citations=[Citation(observation_id=observation.observation_id, relevance="the count")],
        )
        return verdict, store

    def test_the_cited_count_is_read_from_the_observation_not_the_prose(self):
        from nav_sentinel import remediation_cli

        verdict, store = self._verdict(3, cause="there were nine hundred prior errors")
        assert remediation_cli._cited_count(verdict, store, since="2026-07-01") == 3, (
            "the count was parsed from the sentence rather than the cited observation"
        )

    def test_a_verdict_citing_no_count_yields_nothing_to_check(self):
        """A verdict citing *nothing at all* is already impossible -- `Verdict` refuses an asserted
        cause with no citations, which is a stronger control than this test first assumed. The
        reachable case is citing an observation that does not carry the count: it cannot be compared
        against the record, so nothing may be assessed from it."""
        from nav_sentinel import remediation_cli
        from nav_sentinel.control_plane.observations import ObservationStore

        verdict, store = self._verdict(3)
        unrelated = store.as_mapping()["OBS-cited000000000"].model_copy(
            update={"observed": {"units": "101250", "trade_date": "2026-08-17"}}
        )
        other = ObservationStore()
        other.record(unrelated)
        assert remediation_cli._cited_count(verdict, other, since="2026-07-01") is None

    def test_a_disagreement_between_the_officer_and_the_record_stops_the_assessment(
        self, monkeypatch
    ):
        """The control the whole class is about. If the agent says three and the store says five,
        the threshold is in question and proceeding on either would be picking a winner."""
        import sys

        from nav_sentinel import remediation_cli

        # An officer that cites a count the seeded history does not support, and a reporter that
        # answers honestly -- the fake has to answer as whichever agent is asked, or the impact
        # step fails first and the dispute is never reached.
        wrong = self._verdict(99)

        async def fake_investigate(_brief, manifest, **_kwargs):
            if manifest.agent_id == "dealing-impact-reporter":
                return self._dealing_verdict()
            return wrong

        monkeypatch.setattr(
            "nav_sentinel.agents.investigator.investigate", fake_investigate
        )
        monkeypatch.setattr(
            sys, "argv", ["remediation_cli", "--case-id", "CASE-REM-DISPUTE"]
        )
        remediation_cli.main()

        from nav_sentinel import composition
        from nav_sentinel.control_plane import casefile as cf

        parked = cf.load(composition.store(), "CASE-REM-DISPUTE")
        assert parked is not None
        assert parked.stage == "impact_assessed", (
            "the case advanced past a disputed assessment"
        )


class TestTheGovernanceRecordOutlivesTheProcess:
    """The blocker a review found: seven transitions, one span, zero persisted decisions.

    `telemetry.record_policy_decision` returns silently when no span is recording, and nothing on
    this path opened one -- so every P-008 decision reached a per-context list and nothing else.
    Stage history survived and *why* the case moved did not, on the one section whose deliverable is
    a case you can audit three weeks later.
    """

    def test_each_event_opens_its_own_span(self, store, monkeypatch):
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        from nav_sentinel.control_plane import telemetry

        # `tracer()` reads the global provider, which OTel refuses to replace once set. Patching the
        # accessor is the only way to observe spans without a process-wide side effect.
        monkeypatch.setattr(telemetry, "tracer", lambda: provider.get_tracer("test"))
        for entry in ADVANCING:
            _apply(store, entry["event"], occurred_on=entry["occurred_on"])

        cases = [s for s in exporter.get_finished_spans() if s.name == "nav_sentinel.exception_case"]
        assert len(cases) == len(ADVANCING), (
            "one span for a seven-delivery case means six deliveries left no trace"
        )
        assert len({s.context.trace_id for s in cases}) == len(ADVANCING), (
            "the deliveries share a trace id, which OTel cannot produce across invocations"
        )

    def test_every_event_persists_its_decisions(self, store):
        for entry in ADVANCING:
            _apply(store, entry["event"], occurred_on=entry["occurred_on"])
        decisions = store.decisions_for(CASE)
        ids = {d.get("nav.policy.id") for d in decisions}
        assert "P-008-STAGE-TRANSITION" in ids, "no transition decision survived the run"
        assert "P-004-APPROVAL-ROUTE" in ids, (
            "the band derivation was marked after the span opened, so it was never persisted"
        )
        assert len(decisions) == 2 * len(ADVANCING)

    def test_the_decisions_carry_one_trace_id_per_event(self, store):
        for entry in ADVANCING:
            _apply(store, entry["event"], occurred_on=entry["occurred_on"])
        traces = {d["trace_id"] for d in store.decisions_for(CASE)}
        assert len(traces) == len(ADVANCING), (
            "decisions from different deliveries share a trace, so the append-only key collides"
        )

    def test_a_refused_event_still_persists_the_denial(self, store):
        """A rejected delivery that left no durable trace is indistinguishable from one that never
        arrived -- the first question a stalled case raises."""
        _apply(store, "error_detected")
        with pytest.raises(IllegalTransition):
            _apply(store, "approval_recorded")
        denials = [
            d
            for d in store.decisions_for(CASE)
            if d.get("nav.policy.effect") == "deny"
        ]
        assert denials, "the refusal was recorded in memory and persisted nowhere"
        assert denials[0]["nav.policy.id"] == "P-008-STAGE-TRANSITION"

    def test_the_persisted_trail_survives_a_new_repository_handle(self, store):
        """Read back through a fresh view, which is all a later process has."""
        for entry in TIMELINE["events"][:3]:
            _apply(store, entry["event"], occurred_on=entry["occurred_on"])
        reread = store.decisions_for(CASE)
        assert len(reread) == 6
        assert all(d["case_id"] == CASE for d in reread)
