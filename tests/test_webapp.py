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
        assert "P-003" in outcome["agent_posting_blocked"]
        assert "may_post_entries=false" in outcome["agent_posting_blocked"]

    def test_the_refusal_appears_on_the_page(self, client):
        case_id = _four_eyes_case(client)
        for who in (CONTROLLER_A, CONTROLLER_B):
            _signin(client, who)
            client.post(f"/app/case/{case_id}/approve")
        page = client.get(f"/app/case/{case_id}").text
        # The analyst approved; the headline says what happened. The control that holds is stated
        # as the reason the entry is safe to release, not as a failure of their action.
        assert "Cleared for posting" in page
        assert "Posting refused" not in page
        assert "no agent in NAV Sentinel can post it" in page
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


class TestTheFleetPageDoesNotCountASentinelAsAGap:
    """`nav.unclassified` is the value triage returns when no root-cause family fits, and it must
    never have an agent -- routing it would be routing "I do not know" to a specialist. Counting
    the three `.unclassified` capabilities alongside the real gaps reported seven where there are
    four, and put two different things under one label on the page a judge reads."""

    def _page(self):
        from nav_sentinel.control_plane.approvals import Principal
        from nav_sentinel.webapp import pages

        composition.configure()
        return pages.fleet(principal=Principal(subject="a@b.example", role="controller"))

    def test_a_sentinel_is_labelled_as_one_rather_than_as_an_unhandled_capability(self):
        html = self._page()
        assert "sentinel &mdash; always a human" in html
        # The genuine gaps keep the loud treatment; the sentinels must not have it.
        assert html.count("NO PUBLISHED AGENT") == 4, (
            "the count of loudly-unhandled capabilities changed; if a manifest was published or "
            "a capability declared, the narration and README say four"
        )

    def test_the_counts_add_up_to_every_declared_capability(self):
        from nav_sentinel.registry import discover

        composition.configure()
        coverage = discover.coverage()
        sentinels = [c for c, r in coverage.items() if r is None and c.endswith(".unclassified")]
        gaps = [c for c, r in coverage.items() if r is None and c not in sentinels]
        routed = [c for c, r in coverage.items() if r is not None]
        assert len(routed) + len(gaps) + len(sentinels) == len(coverage)
        assert (len(routed), len(gaps), len(sentinels)) == (7, 4, 3), (
            f"routed={len(routed)} gaps={len(gaps)} sentinels={len(sentinels)}; the narration "
            f"says four capabilities have no authorised agent"
        )

    def test_the_page_says_what_happens_to_a_refused_capability(self):
        """The old copy said gaps "escalate loudly", which names no consequence. A viewer asked
        what actually happens to them, and the page could not answer."""
        html = self._page()
        assert "refused at routing" in html
        assert "no agent is invoked" in html
        assert "stays in the queue as human work" in html


class TestTheApprovedStateReadsAsFinished:
    """After approval the rail said three things at once: "Approved — APPR-…", the same grant again
    one box lower, and a disabled button labelled "Approve". Two of those invite the reading that
    something is still expected of you."""

    def _rail(self, *, granted: bool):
        from nav_sentinel.control_plane.approvals import Principal
        from nav_sentinel.webapp import pages

        document = {
            "case_id": "CASE-X",
            "approval_band": "four_eyes",
            "signed_by": ["a@x.example", "b@x.example"],
            "signed_roles": ["controller", "cio"],
        }
        if granted:
            document["approval_ref"] = "APPR-1234567890abcdef"
            document["last_outcome"] = {
                "granted": True,
                "message": "APPR-1234567890abcdef granted at four_eyes by a@x.example, b@x.example",
                "agent_posting_blocked": "[P-003-NO-AUTONOMOUS-POSTING] may_post_entries=false",
            }
        else:
            document["signed_by"] = ["a@x.example"]
            document["signed_roles"] = ["controller"]
            document["last_outcome"] = {
                "granted": False,
                "message": "four_eyes requires 2 distinct signer(s); got 1",
            }
        return pages._actions(
            document,
            Principal(subject="b@x.example", role="cio"),
            "four_eyes",
            list(document["signed_by"]),
            True,
        )

    def test_an_approved_case_offers_no_approve_button(self):
        rail = self._rail(granted=True)
        assert "/approve" not in rail, "a completed approval still offered a button"

    def test_the_grant_is_stated_once(self):
        rail = self._rail(granted=True)
        assert rail.count("APPR-1234567890abcdef") == 1, (
            "the same grant was shown twice, which reads as two things having happened"
        )

    def test_a_refusal_is_still_shown_and_the_button_stays(self):
        """The suppression must apply to grants only — a refusal is the message that matters most."""
        rail = self._rail(granted=False)
        assert "four_eyes requires 2 distinct signer(s); got 1" in rail
        assert "/approve" in rail

    def test_an_analyst_who_has_signed_is_told_what_is_outstanding(self):
        from nav_sentinel.control_plane.approvals import Principal
        from nav_sentinel.webapp import pages

        rail = pages._actions(
            {"case_id": "CASE-X", "approval_band": "four_eyes",
             "signed_by": ["b@x.example"], "signed_roles": ["cio"]},
            Principal(subject="b@x.example", role="cio"),
            "four_eyes",
            ["b@x.example"],
            True,
        )
        assert "waiting for another signatory" in rail


class TestTheApproveButtonSaysWhoCanApprove:
    """A control that can only fail should not be offered.

    A controller looking at a CIO escalation used to get a live **Approve** button, a click, and a
    refusal. The refusal is correct and the server still makes it -- that is the control, and it is
    tested elsewhere -- but an operations screen should not invite an action whose answer it
    already knows.
    """

    @staticmethod
    def _button(rail: str) -> str:
        """The button element alone.

        Asserting `"disabled" not in rail` matched the inline `onsubmit` handler, which sets
        `b.disabled=true` on every form -- a substring that is present however the button renders.
        """
        import re

        match = re.search(r"<button[^>]*>.*?</button>", rail, re.DOTALL)
        assert match, "no button in the rail"
        return match.group(0)

    def _rail(self, *, band: str, role: str, signed=()):
        from nav_sentinel.control_plane.approvals import Principal
        from nav_sentinel.webapp import pages

        document = {
            "case_id": "CASE-X",
            "approval_band": band,
            "signed_by": list(signed),
            "signed_roles": ["controller"] * len(signed),
        }
        return pages._actions(
            document, Principal(subject="me@x.example", role=role), band, list(signed), True
        )

    def test_a_controller_on_an_escalation_is_told_the_cio_signs_it(self):
        button = self._button(self._rail(band="cio_escalation", role="controller"))
        assert "CIO to approve" in button
        assert "disabled" in button

    def test_a_reviewer_on_four_eyes_is_told_which_roles_sign_it(self):
        button = self._button(self._rail(band="four_eyes", role="reviewer"))
        assert "CIO or Controller to approve" in button
        assert "disabled" in button

    def test_the_cio_on_an_escalation_just_sees_approve(self):
        button = self._button(self._rail(band="cio_escalation", role="cio"))
        assert button.endswith(">Approve</button>")
        assert "disabled" not in button

    def test_a_controller_who_may_sign_four_eyes_just_sees_approve(self):
        button = self._button(self._rail(band="four_eyes", role="controller"))
        assert button.endswith(">Approve</button>")
        assert "disabled" not in button

    def test_having_already_signed_is_a_different_message_from_being_unable_to(self):
        """"You have signed and need a second person" is not the same as "you may not sign"."""
        button = self._button(self._rail(band="four_eyes", role="controller", signed=["me@x.example"]))
        assert "waiting for another signatory" in button
        assert "to approve" not in button
        assert "disabled" in button

    def test_the_note_names_your_role_and_the_required_one(self):
        rail = self._rail(band="cio_escalation", role="controller")
        assert "above your signing authority" in rail
        assert "requires <b>CIO</b>" in rail
        assert "Your role on this deployment is Controller" in rail

    def test_the_server_still_refuses_regardless_of_what_the_button_says(self):
        """The button is a courtesy. Removing it must not have moved the control into the page."""
        from nav_sentinel.control_plane.approvals import Principal

        composition.configure()
        store = composition.store()
        store.save_case("CASE-btn", {"case_id": "CASE-btn", "approval_band": "cio_escalation"})
        outcome = workflow.approve(
            "CASE-btn", Principal(subject="controller@x.example", role="controller")
        )
        assert not outcome.granted
        assert store.load_case("CASE-btn").get("signed_by") == []


class TestTheApprovalPanelUsesTheDeskVocabulary:
    """`may_sign` returns the enum's words -- "cio escalation may be signed only by cio; you hold
    controller" is the code talking. It still decides; it no longer writes the sentence."""

    def _line(self, band: str, role: str) -> str:
        import re

        from nav_sentinel.control_plane.approvals import Principal
        from nav_sentinel.webapp import pages

        rail = pages._actions(
            {"case_id": "C", "approval_band": band, "signed_by": [], "signed_roles": []},
            Principal(subject="x@y.example", role=role), band, [], True,
        )
        found = re.search(r"font-size:12\.5px[^>]*>([^<]*)<", rail)
        return found.group(1) if found else ""

    def test_bands_and_roles_are_spelled_the_way_the_desk_says_them(self):
        line = self._line("cio_escalation", "cio")
        assert line == "CIO escalation requires 1 signature from CIO."

    def test_four_eyes_says_the_signatories_must_be_different_people(self):
        line = self._line("four_eyes", "controller")
        assert line.startswith("Four eyes requires 2 signatures from")
        assert "must be different people" in line

    def test_no_enum_spelling_reaches_the_panel(self):
        for band in ("cio_escalation", "four_eyes", "single_reviewer"):
            for role in ("cio", "controller", "reviewer"):
                line = self._line(band, role)
                assert "_" not in line, f"{band}/{role}: {line!r}"
                assert " cio" not in line and line[:3] != "cio", f"{band}/{role}: {line!r}"

    def test_an_ineligible_role_gets_the_explanation_once_not_twice(self):
        """The muted requirement line and the note said the same thing, stacked."""
        assert self._line("cio_escalation", "controller") == ""


class TestEveryRowInTheQueueIsDistinguishable:
    """Two cash cases rendered as "Cash balance difference", identically, for different currencies.

    A row an analyst cannot tell apart from the one above it is a row they have to open to
    identify, which is the opposite of what a queue is for.
    """

    def test_a_cash_case_is_qualified_by_its_currency(self):
        from nav_sentinel.webapp.pages import describe

        assert describe({"break_types": ["cash_balance"], "currency": "EUR"}) == (
            "Cash balance difference · EUR"
        )

    def test_a_security_case_is_qualified_by_its_instrument(self):
        from nav_sentinel.webapp.pages import describe

        assert describe(
            {"break_types": ["market_value"], "isin": "GB00BN7SWP63", "currency": "GBP"}
        ) == "Market value difference · GB00BN7SWP63"

    def test_no_two_rows_in_a_real_cycle_share_a_title(self):
        """The property, rather than the two examples. A new break type that forgets to carry an
        identifier fails here rather than at 6am."""
        composition.configure()
        workflow.run_cycle(workflow.DEFAULT_AS_OF)
        titles = [item.title for item in workflow.queue(workflow.DEFAULT_AS_OF)]
        duplicates = {t for t in titles if titles.count(t) > 1}
        assert not duplicates, f"indistinguishable rows: {sorted(duplicates)}"


class TestBothCycleEntryPointsWriteTheSameCase:
    """The desk and the Pub/Sub handler each built their own projection of a detected case, and
    they had drifted by four fields: the unattended path wrote no break types, no instrument, no
    currency and no basis points.

    So an event-driven cycle produced a queue of rows all titled "Exception" with blank impacts. It
    looked correct in testing only because the desk had usually run first and the merge preserved
    its fields -- the state that exposes it is a store nobody has opened a browser against.
    """

    def test_the_projection_has_one_definition(self):
        import inspect

        from nav_sentinel.pipeline import cycle_runner

        assert "case_document" in inspect.getsource(cycle_runner._persist)
        assert "case_document" in inspect.getsource(workflow.run_cycle)

    def test_a_case_written_by_detection_alone_can_still_be_named(self):
        """Driven through `cycle_runner.run`, which is what Pub/Sub calls, on a store the desk has
        never touched."""
        from datetime import date

        from nav_sentinel.control_plane.repository import InMemoryRepository
        from nav_sentinel.pipeline import cycle_runner
        from nav_sentinel.webapp.pages import describe

        composition.configure()
        previous = composition._repository
        composition._repository = InMemoryRepository()
        try:
            cycle_runner.run(date(2026, 8, 17))
            documents = composition.store().cases_for("MERID-GEF", "2026-08-17")
            assert documents
            for document in documents:
                assert describe(document) != "Exception", document.get("case_id")
                assert document.get("impact_bps"), document.get("case_id")
        finally:
            composition._repository = previous
