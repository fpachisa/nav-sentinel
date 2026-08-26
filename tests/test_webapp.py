"""The exception desk: an analyst signs in, runs the cycle, works a case, and signs it.

These tests exist because the interesting behaviour of this application is its **refusals**, and a
refusal is exactly the kind of thing that looks fine on a screen while doing nothing. Four of them
are load-bearing: a role that may not sign, a single signature where two are required, a signature
that must not be *recorded* when the role is ineligible, and a posting attempt that is refused with a
valid approval in hand.

Everything runs offline. The one step that calls a model -- `work_case` -- is driven directly in the
tests that need its output, never through the model.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from nav_sentinel import composition
from nav_sentinel.server import app
from nav_sentinel.webapp import pages, session, workflow

REVIEWER = "a.okafor@merian.example"
CONTROLLER_A = "j.laurent@merian.example"
CONTROLLER_B = "m.devlin@merian.example"
CIO = "s.raghunathan@merian.example"


@pytest.fixture
def client() -> TestClient:
    composition.configure()
    return TestClient(app, follow_redirects=True)


def _signin(client: TestClient, subject: str) -> None:
    client.post("/app/signout")
    client.post("/app/signin", data={"subject": subject})


def _four_eyes_case(client: TestClient) -> str:
    """A case the control plane bands to four_eyes, with a proposal attached."""
    import re

    # Signed in first: `/app/cycle` ignores an unauthenticated write, which is correct and is why
    # the first version of this helper produced an empty queue.
    _signin(client, CONTROLLER_A)
    client.post("/app/cycle")
    ids = re.findall(r"/app/case/(CASE-[^'\"]+)", client.get("/app").text)
    store = composition.store()
    for case_id in ids:
        document = store.load_case(case_id) or {}
        if document.get("approval_band") == "four_eyes":
            # A known starting state. The store is configured once for the session and case ids are
            # content-derived, so signatures recorded by an earlier test would otherwise carry into
            # this one -- and a test that inherits two signatures cannot observe the first refusal.
            for field in ("signed_by", "signed_roles", "approval_ref", "last_outcome"):
                document.pop(field, None)
            document["verdict"] = {
                "root_cause": "the stale 2026-08-14 USD rate of 1.1567 was applied",
                "confidence": 0.93,
                "citations": [],
                "unresolved": "",
                "agent": "fx-rates-investigator@1.3.0",
            }
            store.save_case(case_id, document)
            return case_id
    raise AssertionError("no four_eyes case in the cycle")


class TestNothingIsVisibleWithoutAnAnalyst:
    def test_the_desk_asks_who_you_are(self, client):
        client.post("/app/signout")
        assert "Sign in" in client.get("/app").text

    def test_a_case_page_asks_too(self, client):
        client.post("/app/signout")
        assert "Sign in" in client.get("/app/case/CASE-ANY").text

    def test_the_signin_route_sets_no_cookie_for_an_unknown_subject(self, client):
        """Isolates the *route's* roster check. Asserting only that the page still says "Sign in"
        could not distinguish it from `verify`'s own roster check -- measured: accepting any subject
        at sign-in left every test green, because `verify` refused it a moment later. Two checks are
        right; a test that cannot tell which one is working is not."""
        client.post("/app/signout")
        client.post("/app/signin", data={"subject": "attacker@example.com"})
        assert not client.cookies.get(session.COOKIE)

    def test_a_signed_cookie_for_a_non_roster_subject_does_not_authenticate(self, client):
        """And the other check: a validly *signed* cookie for someone not on the roster. This is
        what a leaked signing key would let an attacker mint."""
        client.post("/app/signout")
        client.cookies.set(session.COOKIE, session.sign("ghost@example.com"))
        assert "Sign in" in client.get("/app").text
        assert session.verify(session.sign("ghost@example.com")) is None

    def test_a_tampered_cookie_signs_nobody_in(self, client):
        _signin(client, CONTROLLER_A)
        # Same signature, different subject: the classic privilege swap.
        cookie = client.cookies.get(session.COOKIE)
        client.cookies.set(session.COOKIE, f"{CIO}|{cookie.split('|')[1]}")
        assert "Sign in" in client.get("/app").text


class TestTheQueueIsHonestBeforeAnythingRuns:
    def test_the_queue_offers_the_cycle_when_nothing_is_detected(self, client):
        _signin(client, CONTROLLER_A)
        assert "Run reconciliation" in client.get("/app").text

    def test_running_the_cycle_populates_the_queue(self, client):
        _signin(client, CONTROLLER_A)
        client.post("/app/cycle")
        page = client.get("/app").text
        assert "Market value difference" in page or "Cash balance difference" in page

    def test_an_untriaged_case_does_not_display_an_internal_enum(self, client):
        """`nav.unclassified` means triage has not run. Shown as a page title it named nothing and
        made every exception look identical."""
        _signin(client, CONTROLLER_A)
        client.post("/app/cycle")
        assert "nav.unclassified" not in client.get("/app").text

    def test_the_cycle_calls_no_model(self, client, monkeypatch):
        """Detection is arithmetic over two books. A model here would be spending a request to be
        told what subtraction already knows."""
        import nav_sentinel.agents.triage as triage_module

        def explode(*_a, **_k):
            raise AssertionError("the cycle called a model")

        monkeypatch.setattr(triage_module, "classify", explode)
        _signin(client, CONTROLLER_A)
        client.post("/app/cycle")
        assert client.get("/app").status_code == 200


class TestTheFourEyesGateIsRealInTheUi:
    def test_a_reviewer_may_not_sign_a_four_eyes_case(self, client):
        case_id = _four_eyes_case(client)
        _signin(client, REVIEWER)
        client.post(f"/app/case/{case_id}/approve")
        outcome = composition.store().load_case(case_id)["last_outcome"]
        assert outcome["granted"] is False
        assert "may not sign" in outcome["message"]

    def test_an_ineligible_signature_is_not_recorded(self, client):
        """The bug this test exists for: recording the reviewer's signature poisoned every later
        attempt, so two controllers signing afterwards were still refused on role -- the authority
        answering correctly about a record the application should never have built."""
        case_id = _four_eyes_case(client)
        _signin(client, REVIEWER)
        client.post(f"/app/case/{case_id}/approve")
        assert composition.store().load_case(case_id).get("signed_by", []) == []

    def test_one_controller_is_not_enough(self, client):
        case_id = _four_eyes_case(client)
        _signin(client, CONTROLLER_A)
        client.post(f"/app/case/{case_id}/approve")
        outcome = composition.store().load_case(case_id)["last_outcome"]
        assert outcome["granted"] is False
        assert "2 distinct" in outcome["message"]

    def test_two_different_controllers_grant_it(self, client):
        case_id = _four_eyes_case(client)
        for who in (CONTROLLER_A, CONTROLLER_B):
            _signin(client, who)
            client.post(f"/app/case/{case_id}/approve")
        document = composition.store().load_case(case_id)
        assert document["last_outcome"]["granted"] is True
        assert document.get("approval_ref")

    def test_the_same_controller_twice_is_still_one_signature(self, client):
        """Distinct principals, not distinct clicks."""
        case_id = _four_eyes_case(client)
        _signin(client, CONTROLLER_A)
        client.post(f"/app/case/{case_id}/approve")
        client.post(f"/app/case/{case_id}/approve")
        document = composition.store().load_case(case_id)
        assert document["last_outcome"]["granted"] is False
        assert len(set(document["signed_by"])) == 1

    def test_posting_is_refused_with_a_valid_approval_in_hand(self, client):
        """The thesis. An approval is necessary and not sufficient, and the attempt carries the
        real reference -- an attempt without it would be refused for the wrong reason."""
        case_id = _four_eyes_case(client)
        for who in (CONTROLLER_A, CONTROLLER_B):
            _signin(client, who)
            client.post(f"/app/case/{case_id}/approve")
        outcome = composition.store().load_case(case_id)["last_outcome"]
        assert "P-003" in outcome["posting_refused"]
        assert "may_post_entries=false" in outcome["posting_refused"]

    def test_the_refusal_appears_on_the_page(self, client):
        case_id = _four_eyes_case(client)
        for who in (CONTROLLER_A, CONTROLLER_B):
            _signin(client, who)
            client.post(f"/app/case/{case_id}/approve")
        page = client.get(f"/app/case/{case_id}").text
        assert "Posting refused" in page
        assert "P-003" in page


class TestAnActionCannotBeRepeatedByRefreshing:
    def test_every_write_redirects(self, client):
        """Post-redirect-get, so a refresh never re-submits a signature onto a four-eyes case."""
        _signin(client, CONTROLLER_A)
        plain = TestClient(app, follow_redirects=False)
        plain.post("/app/signin", data={"subject": CONTROLLER_A})
        for path in ("/app/cycle", "/app/signout"):
            assert plain.post(path).status_code == 303, path


class TestTheDeskEscapesWhatItRenders:
    def test_a_payload_in_a_verdict_does_not_become_markup(self, client):
        case_id = _four_eyes_case(client)
        store = composition.store()
        document = store.load_case(case_id)
        document["verdict"]["root_cause"] = '<script>alert("x")</script>'
        store.save_case(case_id, document)
        _signin(client, CONTROLLER_A)
        page = client.get(f"/app/case/{case_id}").text
        assert "<script>alert" not in page
        assert "&lt;script&gt;" in page


class TestWhatTheRolesCanSign:
    @pytest.mark.parametrize(
        ("role", "band", "allowed"),
        [
            ("reviewer", "four_eyes", False),
            ("controller", "four_eyes", True),
            ("reviewer", "single_reviewer", True),
            ("controller", "cio_escalation", False),
            ("cio", "cio_escalation", True),
        ],
    )
    def test_role_against_band(self, role, band, allowed):
        from nav_sentinel.control_plane.governance import ApprovalClass

        principal = next(p for p in session.ROSTER if p.role == role)
        assert session.may_sign(principal, ApprovalClass(band))[0] is allowed

    def test_every_roster_role_is_described(self):
        for principal in session.ROSTER:
            assert session.ROLE_NOTES.get(principal.role), principal.role

    def test_every_band_is_reachable_by_someone_on_the_roster(self):
        """A band no analyst can sign is a queue that stalls."""
        from nav_sentinel.control_plane.governance import ApprovalClass

        for band in ApprovalClass:
            assert any(
                session.may_sign(p, band)[0] for p in session.ROSTER
            ), band


class TestTheWorkflowHoldsNoPrivilegeOfItsOwn:
    def test_it_uses_the_composition_roots_authority(self):
        import inspect

        source = inspect.getsource(workflow)
        assert "composition.approval_authority()" in source
        assert "ApprovalAuthority(" not in source, (
            "the web layer constructs its own authority, which is the object the agent runtime is "
            "deliberately never given"
        )

    def test_it_attempts_posting_through_the_gateway(self):
        import inspect

        assert "gateway.authorize_posting" in inspect.getsource(workflow)

    def test_the_pages_never_write(self):
        import inspect

        source = inspect.getsource(pages)
        for forbidden in ("save_case", "record_decision", "record_observation", "grant("):
            assert forbidden not in source, forbidden


class TestTheRemediationPageFindsTheCaseTheStoreActuallyHolds:
    """It resolved its default case id from a local fixture file.

    That worked on a laptop, where the same run had just written the case, and pointed the deployed
    console at an id Firestore had never heard of. The multi-week cross-department case is the
    centre of this project and it rendered as an empty state in the only environment anyone would
    look at it in -- another lookup that was correct everywhere except where it mattered.
    """

    def test_it_prefers_a_case_in_the_store_over_the_fixture(self):
        from nav_sentinel.webapp import routes

        composition.configure()
        store = composition.store()
        store.record_stage(
            "CASE-REM-FROM-THE-STORE",
            1,
            {"to": "detected", "recorded_at": "2026-08-20T09:00:00", "occurred_on": "2026-08-20"},
        )
        assert routes._default_remediation_case() == "CASE-REM-FROM-THE-STORE"

    def test_the_most_recently_written_case_wins(self):
        from nav_sentinel.webapp import routes

        composition.configure()
        store = composition.store()
        for case_id, recorded in (("CASE-REM-OLDER", "2026-07-01T09:00:00"),
                                  ("CASE-REM-NEWER", "2026-09-01T09:00:00")):
            store.record_stage(case_id, 1, {"to": "detected", "recorded_at": recorded})
        assert routes._default_remediation_case() == "CASE-REM-NEWER"

    def test_an_empty_store_still_names_the_case_make_remediation_would_create(self, monkeypatch):
        """The fixture remains the fallback, so an offline run points somewhere meaningful."""
        from nav_sentinel.control_plane.repository import InMemoryRepository
        from nav_sentinel.webapp import routes

        composition.configure()
        monkeypatch.setattr(composition, "store", InMemoryRepository)
        assert routes._default_remediation_case().startswith("CASE-REM-")

    def test_an_unreachable_store_does_not_blank_the_page(self, monkeypatch):
        """A console that 500s because its *default selection* could not be computed would be a
        page taken down by a convenience."""
        from nav_sentinel.webapp import routes

        composition.configure()

        def unreachable():
            raise RuntimeError("Firestore is not answering")

        monkeypatch.setattr(composition, "store", unreachable)
        assert routes._default_remediation_case().startswith("CASE-REM-")
