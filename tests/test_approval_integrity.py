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
