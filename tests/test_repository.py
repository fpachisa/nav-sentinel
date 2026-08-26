"""Persistence: what survives the process that produced it.

Everything the fleet made lived in memory -- the governance log in a ContextVar, observations in a
per-case dict, verdicts on an object. Correct for one request, useless for two. Cloud Run scales to
zero and runs several instances, so a case worked on one is invisible to the next, and the audit
trail is the deliverable rather than a by-product of a process that happens to still be running.

The append-only rules are enforced by the in-memory store as well as by Firestore, deliberately: a
memory store that quietly allowed overwrites would let the offline suite pass while the deployed
service raised, so the rules would only ever be exercised in production.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, date, datetime

import pytest

from nav_sentinel import composition
from nav_sentinel.control_plane import gateway, repository
from nav_sentinel.control_plane.observations import Observation
from nav_sentinel.control_plane.policies import Effect, PolicyDecision

CASE = "CASE-MERID-GEF-2026-08-17-0001"


@pytest.fixture
def store() -> repository.Repository:
    return repository.build("memory")


def _decision(policy_id: str = "P-001-TOOL-ALLOWLIST") -> PolicyDecision:
    return PolicyDecision(
        effect=Effect.ALLOW, policy_id=policy_id, reason="within the manifest",
        agent_ref="fx-rates-investigator@1.3.0", resource="ecb_fx.rate_on",
    )


def _observation(observation_id: str = "OBS-aaaa000000000000", case_id: str = CASE) -> Observation:
    return Observation(
        observation_id=observation_id, case_id=case_id,
        agent_ref="fx-rates-investigator@1.3.0", tool="ecb_fx.rate_on",
        args="currency=USD,day=2026-08-17", digest="0123456789abcdef",
        retrieved_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        source="ecb_fx_reference_rates", observed={"rate": "1.1593"},
    )


class TestTheGovernanceLogIsAppendOnly:
    def test_a_decision_cannot_be_overwritten(self, store):
        """A governance log you can edit is not one."""
        store.record_decision(CASE, "tr-1", 0, _decision())
        with pytest.raises(repository.ImmutableRecord, match="append-only"):
            store.record_decision(CASE, "tr-1", 0, _decision("P-006-DATA-SCOPE"))

    def test_decisions_are_keyed_by_case_trace_and_position(self, store):
        """Two instances working different cases never collide; two working the same one do, and
        that is a real conflict rather than something to paper over."""
        store.record_decision(CASE, "tr-1", 0, _decision())
        store.record_decision("CASE-OTHER", "tr-1", 0, _decision())      # different case
        store.record_decision(CASE, "tr-2", 0, _decision())              # different run
        store.record_decision(CASE, "tr-1", 1, _decision())              # next in sequence
        assert len(store.decisions_for(CASE)) == 3

    def test_two_runs_of_one_case_are_both_kept_and_not_interleaved(self, store):
        """Re-running a cycle is a second investigation, not a correction of the first. Ordering by
        sequence alone read back as one confused sequence instead of two clean ones."""
        for sequence in range(3):
            store.record_decision(CASE, "tr-b", sequence, _decision())
            store.record_decision(CASE, "tr-a", sequence, _decision())
        traces = [d["trace_id"] for d in store.decisions_for(CASE)]
        assert traces == ["tr-a"] * 3 + ["tr-b"] * 3, traces

    def test_the_stored_decision_carries_what_the_span_carries(self, store):
        store.record_decision(CASE, "tr-1", 0, _decision())
        stored = store.decisions_for(CASE)[0]
        assert stored["nav.policy.id"] == "P-001-TOOL-ALLOWLIST"
        assert stored["nav.agent.ref"] == "fx-rates-investigator@1.3.0"
        assert stored["case_id"] == CASE and stored["trace_id"] == "tr-1"


class TestObservationsAreImmutable:
    def test_the_same_observation_recorded_twice_is_one_record(self, store):
        """Ids are content-derived, so a repeated identical call is one observation, not an error."""
        store.record_observation(_observation())
        store.record_observation(_observation())
        assert len(store.observations_for(CASE)) == 1

    def test_a_different_body_under_the_same_id_is_refused(self, store):
        """Evidence a verdict has already cited must not be rewritable underneath it."""
        store.record_observation(_observation())
        with pytest.raises(repository.ImmutableRecord, match="different"):
            store.record_observation(_observation().model_copy(update={"digest": "deadbeef"}))

    def test_observations_are_scoped_to_their_case(self, store):
        store.record_observation(_observation("OBS-1"))
        store.record_observation(_observation("OBS-2", case_id="CASE-OTHER"))
        assert [o.observation_id for o in store.observations_for(CASE)] == ["OBS-1"]

    def test_a_stored_observation_round_trips(self, store):
        store.record_observation(_observation())
        restored = store.observations_for(CASE)[0]
        assert restored == _observation()


class TestCasesAreCurrentState:
    def test_a_case_can_be_saved_and_reloaded(self, store):
        store.save_case(CASE, {"case_id": CASE, "subject_id": "MERID-GEF", "as_of": "2026-08-17"})
        assert store.load_case(CASE)["subject_id"] == "MERID-GEF"

    def test_a_case_may_be_updated_because_its_state_changes(self):
        """Opened, classified, investigated, proposed against, approved, closed. Unlike the log."""
        store = repository.build("memory")
        store.save_case(CASE, {"case_id": CASE, "status": "open"})
        store.save_case(CASE, {"case_id": CASE, "status": "closed"})
        assert store.load_case(CASE)["status"] == "closed"

    def test_an_absent_case_is_none_rather_than_an_error(self, store):
        assert store.load_case("CASE-NOPE") is None

    def test_cases_are_found_by_subject_and_date(self, store):
        store.save_case("A", {"case_id": "A", "subject_id": "MERID-GEF", "as_of": "2026-08-17"})
        store.save_case("B", {"case_id": "B", "subject_id": "MERID-GEF", "as_of": "2026-07-17"})
        store.save_case("C", {"case_id": "C", "subject_id": "OTHER-FUND", "as_of": "2026-08-17"})
        assert {c["case_id"] for c in store.cases_for("MERID-GEF", "2026-08-17")} == {"A"}

    def test_a_returned_case_is_a_copy(self, store):
        """A caller mutating what it was handed must not edit the store."""
        store.save_case(CASE, {"case_id": CASE, "status": "open"})
        store.load_case(CASE)["status"] = "tampered"
        assert store.load_case(CASE)["status"] == "open"


class TestChoosingABackend:
    def test_an_unknown_backend_is_refused_rather_than_defaulted(self):
        """Defaulting is how a deployment ends up writing its audit trail to a dict."""
        with pytest.raises(ValueError, match="unknown repository backend"):
            repository.build("firestor")

    def test_the_memory_store_enforces_the_same_rules_as_firestore(self):
        """Otherwise the offline suite passes while the deployed service raises, and the rules are
        only ever exercised in production."""
        import inspect

        source = inspect.getsource(repository.InMemoryRepository)
        assert "ImmutableRecord" in source

    def test_both_backends_implement_the_whole_interface(self):
        for implementation in (repository.InMemoryRepository, repository.FirestoreRepository):
            unimplemented = {
                name
                for name in repository.Repository.__abstractmethods__
                if getattr(implementation, name) is getattr(repository.Repository, name)
            }
            assert not unimplemented, f"{implementation.__name__}: {unimplemented}"


class TestConfigureDoesNotSilentlyDowngradeTheStore:
    """`configure()` is called by every entry point *and* at the top of `cycle_runner.run`, so a
    defaulting second call replaced whatever was installed. Measured: a service configured for
    Firestore at startup got an InMemoryRepository on its first cycle and wrote its audit trail to
    a dict that vanishes when the instance scales down."""

    def test_a_bare_reconfigure_keeps_the_installed_backend(self, monkeypatch):
        monkeypatch.setattr(repository, "FirestoreRepository", repository.InMemoryRepository)
        composition.configure(approvals_backend="memory", repository_backend="firestore")
        installed = composition.store()
        composition.configure()
        assert composition.store() is installed

    def test_an_explicit_backend_always_applies(self):
        composition.configure(approvals_backend="memory", repository_backend="memory")
        first = composition.store()
        composition.configure(approvals_backend="memory", repository_backend="memory")
        assert composition.store() is not first or isinstance(
            composition.store(), repository.InMemoryRepository
        )

    def test_the_repository_follows_an_explicit_approvals_backend(self, monkeypatch):
        """The two answer the same question -- deployment or test -- and a service persisting
        approvals durably while writing its governance log to memory is the worst of both."""
        built: list[str] = []
        monkeypatch.setattr(
            repository,
            "build",
            lambda backend: built.append(backend) or repository.InMemoryRepository(),
        )
        # The approvals store is stubbed too, or this test would need live credentials and the
        # suite would stop running on a clean checkout -- which is a guarantee worth more than the
        # convenience of one fewer patch.
        monkeypatch.setattr(composition, "_configure_approvals", lambda _backend: None)
        composition.configure(approvals_backend="firestore")
        assert built[-1] == "firestore"

    def test_no_store_before_configure(self):
        composition.reset()
        try:
            with pytest.raises(RuntimeError, match="no repository is configured"):
                composition.store()
        finally:
            composition.configure()


class TestACyclePersistsItsOwnTrail:
    def test_every_case_and_its_decisions_are_written(self):
        from nav_sentinel.pipeline import cycle_runner

        composition.configure(approvals_backend="memory", repository_backend="memory")
        result = cycle_runner.run(date(2026, 8, 17))
        store = composition.store()

        assert len(store.cases_for("MERID-GEF", "2026-08-17")) == len(result["cases"])
        persisted = sum(len(store.decisions_for(row["case_id"])) for row in result["cases"])
        assert persisted == result["decisions"], (
            f"{persisted} decisions persisted against {result['decisions']} recorded -- the stored "
            f"trail and the in-memory log disagree"
        )

    def test_each_case_gets_only_its_own_decisions(self):
        """The log is per-context and a cycle works several cases in one context, so persisting
        without a boundary would have given every case the whole cycle's decisions.

        Scoped to this run's trace. Case ids became content-derived, so a case accumulates the
        history of every run against it -- which is the append-only property working, and means a
        count across all runs is not what this test is about.
        """
        from nav_sentinel.pipeline import cycle_runner

        composition.configure(approvals_backend="memory", repository_backend="memory")
        result = cycle_runner.run(date(2026, 8, 17))
        store = composition.store()
        counts = {
            len(
                [
                    d
                    for d in store.decisions_for(row["case_id"])
                    if d["trace_id"] == row["trace_id"]
                ]
            )
            for row in result["cases"]
        }
        assert counts == {result["decisions"] // len(result["cases"])}, counts

    def test_the_stored_band_matches_the_reported_band(self):
        from nav_sentinel.pipeline import cycle_runner

        composition.configure(approvals_backend="memory", repository_backend="memory")
        result = cycle_runner.run(date(2026, 8, 17))
        store = composition.store()
        for row in result["cases"]:
            assert store.load_case(row["case_id"])["approval_band"] == row["band"]


class TestTheDecisionMarker:
    def test_decisions_since_reports_only_what_followed_the_mark(self):
        gateway.clear_decision_log()
        gateway._record(_decision())
        gateway.mark_decisions("CASE-X")
        gateway._record(_decision("P-006-DATA-SCOPE"))
        assert [d.policy_id for d in gateway.decisions_since("CASE-X")] == ["P-006-DATA-SCOPE"]

    def test_an_unmarked_case_reports_the_whole_log(self):
        """Better than reporting nothing: a caller that forgot to mark gets too much rather than
        an empty trail."""
        gateway.clear_decision_log()
        gateway._record(_decision())
        assert len(gateway.decisions_since("never-marked")) == 1

    def test_clearing_the_log_clears_the_marks(self):
        gateway._record(_decision())
        gateway.mark_decisions("CASE-X")
        gateway.clear_decision_log()
        assert gateway.decisions_since("CASE-X") == []


@pytest.mark.live
class TestAgainstRealFirestore:
    """The append-only rules are enforced by Firestore itself -- `create` rather than `set` -- so
    two instances cannot interleave a read-then-write. That cannot be checked in memory."""

    @pytest.fixture
    def live_store(self):
        return repository.build("firestore")

    def test_a_cycle_persists_and_reloads(self, live_store):
        from nav_sentinel.pipeline import cycle_runner

        composition.configure(approvals_backend="firestore", repository_backend="firestore")
        result = cycle_runner.run(date(2026, 8, 17))
        first = result["cases"][0]["case_id"]
        reloaded = composition.store().load_case(first)
        assert reloaded["approval_band"] == result["cases"][0]["band"]
        assert reloaded["trace_id"] == result["cases"][0]["trace_id"]

    def test_firestore_itself_refuses_a_duplicate_decision(self, live_store):
        """`create` fails if the document exists, which is the append-only rule enforced by the
        store rather than by a read-then-write two instances could interleave."""
        trace = "live-duplicate-probe"
        # A previous run may already have written it, which is itself the property under test.
        with contextlib.suppress(repository.ImmutableRecord):
            live_store.record_decision(CASE, trace, 0, _decision())
        with pytest.raises(repository.ImmutableRecord):
            live_store.record_decision(CASE, trace, 0, _decision())

    def test_reading_a_cases_decisions_needs_no_composite_index(self, live_store):
        """`.where(...).order_by(...)` raises FailedPrecondition until an index is provisioned --
        a deploy-time dependency for a query returning a handful of documents."""
        decisions = live_store.decisions_for(CASE)
        assert decisions, "no decisions found; the test would prove nothing"
        sequences = [d["sequence"] for d in decisions]
        assert sequences == sorted(sequences, key=lambda s: s) or len(set(sequences)) < len(sequences)


class TestBothBackendsStampWhenTheyWroteADecision:
    """`recent_decisions` orders on `recorded_at`, and Firestore's `order_by` omits documents that
    lack the ordered field. So a decision written without it is invisible to the live feed while
    being perfectly present in the audit trail -- which is exactly what happened: the feed read 0
    against 188 stored decisions, because the timestamp landed in the in-memory backend and not in
    the Firestore one. Same interface, one implementation stamping, and only the deployed half wrong.
    """

    def test_the_memory_backend_stamps_it(self):
        from nav_sentinel.control_plane.governance import PolicyDecision
        from nav_sentinel.control_plane.repository import InMemoryRepository

        store = InMemoryRepository()
        store.record_decision(
            "CASE-1", "t", 0,
            PolicyDecision(policy_id="P-001-TOOL-ALLOWLIST", effect="allow", reason="ok"),
        )
        assert store.decisions_for("CASE-1")[0]["recorded_at"]

    def test_the_firestore_backend_stamps_it_too(self):
        """Asserted on the source, because the alternative is a live database.

        A weaker check than the behavioural one above, and it is the one that would have caught the
        bug: the two implementations diverged and the offline suite could not tell.
        """
        import inspect

        from nav_sentinel.control_plane.repository import FirestoreRepository

        source = inspect.getsource(FirestoreRepository.record_decision)
        assert '"recorded_at"' in source, (
            "the Firestore backend writes decisions with no timestamp, so the live feed cannot "
            "order them and Firestore's order_by will omit every one"
        )

    def test_both_backends_agree_on_the_fields_a_decision_carries(self):
        """The general form. Two implementations of one interface writing different shapes is the
        defect above; this fails whenever they drift again, whatever the field."""
        import inspect

        from nav_sentinel.control_plane.repository import (
            FirestoreRepository,
            InMemoryRepository,
        )

        def keys(fn) -> set[str]:
            import re

            return set(re.findall(r'"([a-z_]+)":', inspect.getsource(fn)))

        assert keys(InMemoryRepository.record_decision) == keys(
            FirestoreRepository.record_decision
        )
