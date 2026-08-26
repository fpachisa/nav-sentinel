"""What a signature is a signature *for*, and what happens when two people sign at once.

A fresh-context review of the approval console found three ways a signature could end up attached
to something nobody signed. All three share a shape: the console was built as load-mutate-save on a
single case document, and the offline suite is single-threaded, single-request and drives one code
path at a time -- so the states that expose them are states no test constructed.

Four-eyes is the control this project exists to demonstrate. It is also, by definition, the one
control that puts two humans on the same document at the same moment.
"""

from __future__ import annotations

import threading

import pytest

from nav_sentinel import composition
from nav_sentinel.control_plane.approvals import Principal
from nav_sentinel.webapp import workflow

CONTROLLER_A = Principal(subject="j.laurent@merian.example", role="controller")
CONTROLLER_B = Principal(subject="m.devlin@merian.example", role="controller")
REVIEWER = Principal(subject="a.okafor@merian.example", role="reviewer")


@pytest.fixture
def case() -> str:
    """A four-eyes case with a proposal on it, seeded directly rather than through a model."""
    composition.configure()
    store = composition.store()
    case_id = "CASE-approval-integrity"
    store.save_case(
        case_id,
        {
            "case_id": case_id,
            "subject_id": "LU0000000001",
            "as_of": "2026-08-17",
            "approval_band": "four_eyes",
            "proposal": {"proposal_id": "PROP-original", "lines": [{"account": "cash"}]},
        },
    )
    return case_id


class TestTwoPeopleSigningAtOnceBothCount:
    def test_concurrent_signatures_do_not_overwrite_each_other(self, case):
        """The lost update, produced rather than argued about.

        Load-mutate-save meant whichever signature committed first was overwritten by the second
        analyst's whole-document write. It fails *safe* -- a signature is dropped, never invented --
        which is precisely why it was invisible: the case simply stayed unapproved, and the obvious
        reading is that someone had not signed yet.
        """
        start = threading.Barrier(2)
        outcomes: dict[str, workflow.ApprovalOutcome] = {}

        def sign(principal: Principal) -> None:
            start.wait()
            outcomes[principal.subject] = workflow.approve(case, principal)

        threads = [
            threading.Thread(target=sign, args=(CONTROLLER_A,)),
            threading.Thread(target=sign, args=(CONTROLLER_B,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        document = composition.store().load_case(case)
        assert sorted(document["signed_by"]) == sorted(
            [CONTROLLER_A.subject, CONTROLLER_B.subject]
        ), f"a signature was lost: {document['signed_by']}"
        assert document.get("approval_ref"), "both controllers signed and the case is not approved"
        assert any(o.granted for o in outcomes.values())


class TestASignatureIsVoidWhenWhatItSignedChanges:
    def test_re_working_a_case_discards_the_signatures_on_the_old_proposal(self, case):
        """The substitution. Two controllers approve a correcting entry; the case is re-worked and
        a *different* entry replaces it; the signatures stayed valid and the approval reference
        stayed attached. The record then showed two named people having approved a journal entry
        neither of them ever saw."""
        store = composition.store()
        workflow.approve(case, CONTROLLER_A)
        granted = workflow.approve(case, CONTROLLER_B)
        assert granted.granted
        signed_ref = store.load_case(case)["approval_ref"]

        document = store.load_case(case)
        document["proposal"] = {
            "proposal_id": "PROP-substituted",
            "lines": [{"account": "cash", "debit": "9999999.00"}],
        }
        store.save_case(case, document)

        after = workflow.approve(case, CONTROLLER_A)
        assert not after.granted, "a substituted proposal inherited a completed approval"
        assert after.outstanding == 1
        current = store.load_case(case)
        assert current["signed_by"] == [CONTROLLER_A.subject]
        assert current.get("approval_ref") != signed_ref

    def test_signatures_collected_at_four_eyes_do_not_satisfy_a_lower_band_alone(self, case):
        """One controller signs toward four-eyes. The case is then re-scored to single_reviewer --
        which `work_case` recomputes on every run. That one signature must not silently become a
        complete approval at the lower band."""
        store = composition.store()
        first = workflow.approve(case, CONTROLLER_A)
        assert not first.granted

        document = store.load_case(case)
        document["approval_band"] = "single_reviewer"
        store.save_case(case, document)

        after = workflow.approve(case, CONTROLLER_A)
        assert store.load_case(case)["signed_by"] == [CONTROLLER_A.subject]
        assert after.granted, "the analyst re-signed at the new band, which is a real approval"
        # The point: it took a *fresh* signature at the new band. The stale one was discarded.
        assert store.load_case(case)["signed_for"].startswith("single_reviewer")

    def test_an_ineligible_signature_is_still_not_recorded(self, case):
        """Unchanged behaviour, re-asserted because the code around it was rewritten."""
        store = composition.store()
        outcome = workflow.approve(case, REVIEWER)
        assert not outcome.granted
        assert store.load_case(case).get("signed_by") == []

        workflow.approve(case, CONTROLLER_A)
        assert workflow.approve(case, CONTROLLER_B).granted, (
            "the reviewer's refused attempt poisoned the two controllers who followed"
        )


class TestACaseWrittenByAnOlderDeployStillOpens:
    def test_a_document_with_no_signed_roles_does_not_raise(self, case):
        """`zip(..., strict=True)` raised on any case document written before signatures carried
        roles. Firestore documents outlive the deploy that wrote them, so this is a 500 on a real
        case in a real deployment, reachable only by having deployed twice."""
        store = composition.store()
        document = store.load_case(case)
        document["signed_by"] = ["someone.from.last.month@merian.example"]
        document.pop("signed_roles", None)
        document["signed_for"] = workflow._signed_for(document)
        store.save_case(case, document)

        outcome = workflow.approve(case, CONTROLLER_A)
        assert not outcome.granted  # one eligible signature of two, which is the honest answer


class TestInvestigatingTheSameCaseTwiceIsNotTampering:
    """Re-working a case crashed against Firestore with `ImmutableRecord`.

    An observation's id is derived from `(case_id, tool, args, digest)` -- deliberately not from
    `retrieved_at` or `trace_id`, because a citation has to be reproducible to be checkable. But
    the immutability check compared the *whole* record, so a second investigation that re-made the
    same call derived the same id, presented a record differing only in when it happened and which
    run it belonged to, and was rejected as a changed audit record.

    It could not be caught offline: an offline run begins with an empty store, so the second write
    never happens. The in-memory backend claims in its own docstring to enforce "the same
    append-only rules as Firestore" and was in fact stricter in a way nothing exercised.
    """

    def _observation(self, **overrides):
        from datetime import UTC, datetime

        from nav_sentinel.control_plane.observations import Observation

        base = {
            "observation_id": "OBS-deadbeefdeadbeef",
            "case_id": "CASE-x",
            "trace_id": "trace-one",
            "agent_ref": "fx-rates-investigator@1.3.0",
            "tool": "ecb_fx.rate_on",
            "args": "on=2026-08-17,pair=USDEUR",
            "digest": "abc123",
            "retrieved_at": datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
            "source": "ECB",
            "observed": {"rate": "1.1489"},
        }
        return Observation(**{**base, **overrides})

    def test_the_same_call_in_a_later_run_is_accepted_and_the_first_record_stands(self):
        from datetime import UTC, datetime

        store = composition.store()
        first = self._observation()
        store.record_observation(first)

        # A second investigation: same call, same result, new run, later clock.
        store.record_observation(
            self._observation(
                trace_id="trace-two", retrieved_at=datetime(2026, 9, 1, 14, 30, tzinfo=UTC)
            )
        )

        held = [o for o in store.observations_for("CASE-x") if o.observation_id == first.observation_id]
        assert len(held) == 1
        assert held[0].retrieved_at == first.retrieved_at, (
            "a cited retrieved_at should be when the data was obtained, not when it was re-read"
        )

    def test_a_genuinely_different_body_under_the_same_id_is_still_refused(self):
        """The guard still guards. Same id, different facts, means the derivation changed."""
        from nav_sentinel.control_plane.repository import ImmutableRecord

        store = composition.store()
        store.record_observation(self._observation(observation_id="OBS-cafecafecafecafe"))
        with pytest.raises(ImmutableRecord):
            store.record_observation(
                self._observation(
                    observation_id="OBS-cafecafecafecafe", observed={"rate": "9.9999"}
                )
            )


class TestDetectionDoesNotDestroyWorkAlreadyDone:
    """Re-running detection erased every worked case.

    `cycle_runner._persist` wrote the case with a blind whole-document `set()`, so a second
    detection pass deleted the verdict, the drafted correction, the signatures and the approval
    reference of every case an analyst had already worked and signed. The desk's own "Re-run
    reconciliation" button was safe because `workflow.run_cycle` merges; the Pub/Sub path and
    `make demo` were not.

    The worst kind of quiet write: it costs nothing, reports success, and the queue afterwards looks
    like a clean starting state rather than a loss. One stray `gcloud pubsub topics publish` during
    a rehearsal would have wiped the take.
    """

    def _worked(self, store, case_id: str) -> None:
        document = store.load_case(case_id)
        document.update(
            {
                "verdict": {"root_cause": "a stale rate", "confidence": 0.93, "agent": "fx@1"},
                "proposal": {"proposal_id": "PROP-original"},
                "signed_by": ["a.controller@merian.example"],
                "signed_roles": ["controller"],
                "approval_ref": "APPR-abcdef0123456789",
            }
        )
        store.save_case(case_id, document)

    def test_a_second_detection_pass_keeps_the_verdict_and_the_signatures(self):
        from datetime import date

        from nav_sentinel.pipeline import cycle_runner
        from nav_sentinel.webapp import workflow

        composition.configure()
        as_of = date(2026, 8, 17)
        workflow.run_cycle(as_of)
        store = composition.store()
        case_id = workflow.queue(as_of)[0].case_id
        self._worked(store, case_id)

        cycle_runner.run(as_of)  # arithmetic only; no model is called

        after = store.load_case(case_id)
        assert after["verdict"]["root_cause"] == "a stale rate"
        assert after["proposal"]["proposal_id"] == "PROP-original"
        assert after["signed_by"] == ["a.controller@merian.example"]
        assert after["approval_ref"] == "APPR-abcdef0123456789"

    def test_detection_still_refreshes_the_fields_it_owns(self):
        """Merging must not turn the write into a no-op: a re-scored band has to land."""
        from datetime import date

        from nav_sentinel.pipeline import cycle_runner
        from nav_sentinel.webapp import workflow

        composition.configure()
        as_of = date(2026, 8, 17)
        workflow.run_cycle(as_of)
        store = composition.store()
        case_id = workflow.queue(as_of)[0].case_id

        document = store.load_case(case_id)
        document["approval_band"] = "auto_clear"
        document["break_ids"] = []
        store.save_case(case_id, document)

        cycle_runner.run(as_of)

        after = store.load_case(case_id)
        assert after["approval_band"] != "auto_clear", "detection did not re-score the band"
        assert after["break_ids"], "detection did not rewrite the breaks it found"

    def test_a_first_detection_pass_still_creates_the_case(self):
        """`update_case` raises when there is no document, which is the normal first run."""
        from datetime import date

        from nav_sentinel.control_plane.repository import InMemoryRepository
        from nav_sentinel.pipeline import cycle_runner

        composition.configure()
        composition._repository = InMemoryRepository()
        cycle_runner.run(date(2026, 8, 17))
        assert composition.store().cases_for("MERID-GEF", "2026-08-17"), "no cases were created"
