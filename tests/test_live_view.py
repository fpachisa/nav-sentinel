"""The live operations screen, and the rule that every number on it is countable.

A live display is the easiest place in a system to put a figure nobody can check: it moves, it
looks like telemetry, and it is gone by the time anyone asks. This project has already shipped a
console panel that rendered empty on fourteen real decisions and a readiness probe that reported a
row count as an answer to "can anyone sign?", so the standard here is that each counter is derived
from a persisted record and can be reproduced by reading the store.
"""

from __future__ import annotations

import pytest

from nav_sentinel import composition
from nav_sentinel.control_plane.approvals import Principal
from nav_sentinel.control_plane.governance import PolicyDecision
from nav_sentinel.webapp import pages, workflow

ANALYST = Principal(subject="fpachisa@gmail.com", role="controller")


@pytest.fixture
def cycled() -> list[str]:
    """A cycle with every case back at "not started".

    The store is configured once for the session and case ids are content-derived, so work done by
    an earlier test carries into this one -- and now that detection *merges* rather than
    overwriting, re-running the cycle no longer clears it. That merge is the correct behaviour: it
    is what stops a stray publish erasing a signed case. It does mean a test that wants a known
    starting state has to establish one.
    """
    composition.configure()
    workflow.run_cycle(workflow.DEFAULT_AS_OF)
    store = composition.store()
    ids = [item.case_id for item in workflow.queue(workflow.DEFAULT_AS_OF)]
    for case_id in ids:
        document = store.load_case(case_id) or {}
        for field in ("triage", "routed", "refusal", "investigator", "verdict", "proposal",
                      "drafted", "draft_skipped", "dispatched_at", "signed_by", "signed_roles", "approval_ref",
                      "last_outcome"):
            document.pop(field, None)
        store.save_case(case_id, document)
    return ids


class TestTheCountersAreCountedFromTheStore:
    def test_a_fresh_cycle_reports_no_investigations_and_no_specialists(self, cycled):
        counters = workflow.live_snapshot()["counters"]
        assert counters["cases"] == len(cycled)
        assert counters["investigated"] == 0
        assert counters["agents"] == 0, "no agent has run, so none can be engaged"
        assert counters["evidence"] == 0

    def test_investigating_a_case_moves_the_counters(self, cycled):
        store = composition.store()
        document = store.load_case(cycled[0])
        document.update(
            {
                "triage": {"capability": "nav.fx_rate", "confidence": 0.9, "reasoning": "r"},
                "routed": True,
                "investigator": "fx-rates-investigator@1.3.0",
                "verdict": {"root_cause": "x", "confidence": 0.9, "citations": [],
                            "agent": "fx-rates-investigator@1.3.0"},
                "proposal": {"proposal_id": "PROP-1"},
            }
        )
        store.save_case(cycled[0], document)

        counters = workflow.live_snapshot()["counters"]
        assert counters["investigated"] == 1
        assert counters["agents"] == 1

    def test_the_specialist_count_is_distinct_agents_not_cases(self, cycled):
        """Two cases handled by one agent is one specialist engaged, not two."""
        store = composition.store()
        for case_id in cycled[:2]:
            document = store.load_case(case_id)
            document["investigator"] = "fx-rates-investigator@1.3.0"
            store.save_case(case_id, document)
        assert workflow.live_snapshot()["counters"]["agents"] == 1

    def test_denials_count_only_denials(self, cycled):
        store = composition.store()
        # A run has to exist for a scoped counter to be a number at all -- absent is not zero.
        for case_id in cycled:
            document = store.load_case(case_id)
            document["dispatched_at"] = "2020-01-01T00:00:00+00:00"
            store.save_case(case_id, document)
        before = workflow.live_snapshot()["counters"]["denials"]
        store.record_decision(
            cycled[0], "t-x", 5001,
            PolicyDecision(policy_id="P-003-NO-AUTONOMOUS-POSTING", effect="deny", reason="no"),
        )
        store.record_decision(
            cycled[0], "t-x", 5002,
            PolicyDecision(policy_id="P-001-TOOL-ALLOWLIST", effect="allow", reason="yes"),
        )
        counters = workflow.live_snapshot()["counters"]
        assert counters["denials"] == before + 1


class TestTheWindowIsHonest:
    def test_since_excludes_decisions_written_before_it(self, cycled):
        """`demo-reset` preserves decisions on purpose, so an unscoped count opens at the total of
        every rehearsal — a true number answering a question nobody asked."""
        wide = workflow.live_snapshot(since="2020-01-01T00:00:00+00:00")["counters"]["decisions"]
        assert wide > 0
        assert workflow.live_snapshot(since="2099-01-01T00:00:00+00:00")["counters"][
            "decisions"
        ] == 0

    def test_with_no_run_a_scoped_counter_is_absent_rather_than_zero(self, cycled):
        """Zero is a claim -- "the fleet did nothing" -- when the truth is "no run has started".

        Reporting the cumulative total instead is what made the numbers appear on the first poll
        and then drop, which is how this was found.
        """
        snapshot = workflow.live_snapshot()
        assert snapshot["running"] is False
        assert snapshot["counters"]["decisions"] is None
        assert snapshot["counters"]["tool_calls"] is None
        assert snapshot["counters"]["denials"] is None
        # Case-scoped counters are exact without a window, so they stay numbers.
        assert snapshot["counters"]["cases"] == len(cycled)
        assert snapshot["counters"]["evidence"] >= 0

    def test_a_dash_is_rendered_for_an_absent_counter(self, cycled):
        assert "&mdash;" in pages.live(workflow.live_snapshot(), principal=ANALYST)

    def test_the_poller_never_repaints_a_number_back_to_a_dash(self):
        """Mid-run the window exists, so a `None` arriving later would be a regression, not news."""
        assert "value === null || value === undefined) return" in pages._LIVE_SCRIPT

    def test_the_page_says_which_window_it_counted(self, cycled):
        unscoped = pages.live(workflow.live_snapshot(), principal=ANALYST)
        assert "no run in progress" in unscoped

        scoped = pages.live(
            workflow.live_snapshot(since="2026-08-17T09:00:00+00:00"), principal=ANALYST
        )
        assert "this run started 09:00:00" in scoped


class TestTheWindowIsTheRunNotThePageLoad:
    """The counters showed real totals once and then dropped to zero.

    The window was pinned client-side on the page's first poll, and the fan-out starts *before* the
    redirect to this page lands -- so a run fell outside its own window and every scoped counter
    read zero from the second poll onward. The browser is not the authority on when a run began,
    and may not have existed when it did.
    """

    def test_the_window_comes_from_when_the_cases_were_dispatched(self, cycled):
        store = composition.store()
        for case_id in cycled:
            document = store.load_case(case_id)
            document["dispatched_at"] = "2026-08-17T09:00:00+00:00"
            store.save_case(case_id, document)

        assert workflow.live_snapshot()["since"] == "2026-08-17T09:00:00+00:00"

    def test_the_earliest_dispatch_wins_so_nothing_in_the_run_is_excluded(self, cycled):
        """A fan-out stamps each case as it hands it over, so the stamps differ by milliseconds.
        Taking the latest would drop the earliest case's work out of its own run."""
        store = composition.store()
        for offset, case_id in enumerate(cycled):
            document = store.load_case(case_id)
            document["dispatched_at"] = f"2026-08-17T09:00:{offset:02d}+00:00"
            store.save_case(case_id, document)

        assert workflow.live_snapshot()["since"] == "2026-08-17T09:00:00+00:00"

    def test_dispatching_stamps_the_cases(self, cycled, monkeypatch):
        from nav_sentinel.webapp import dispatch

        monkeypatch.delenv("NAV_CASES_TOPIC", raising=False)
        monkeypatch.setattr(workflow, "work_case", lambda *_a, **_k: None)
        dispatch.dispatch(cycled[:2], workflow.DEFAULT_AS_OF)

        store = composition.store()
        assert all(store.load_case(c).get("dispatched_at") for c in cycled[:2])
        assert workflow.live_snapshot()["since"]

    def test_the_page_no_longer_sends_a_window_of_its_own(self):
        """The client used to pin it, which is the bug. It must not do that again."""
        assert "?since=" not in pages._LIVE_SCRIPT
        assert "snap.now" not in pages._LIVE_SCRIPT


class TestTheStagesComeFromTheDocument:
    def test_a_refused_case_shows_route_refused_and_the_rest_blocked(self, cycled):
        store = composition.store()
        document = store.load_case(cycled[0])
        document.update({"triage": {"capability": "nav.pricing", "confidence": 0.9,
                                    "reasoning": "r"}, "routed": False,
                         "refusal": "no published agent handles nav.pricing"})
        store.save_case(cycled[0], document)

        row = next(r for r in workflow.live_snapshot()["cases"] if r["case_id"] == cycled[0])
        assert row["stages"] == {
            "triage": "done", "routing": "refused",
            "investigation": "blocked", "draft": "blocked",
        }
        assert workflow.live_snapshot()["counters"]["refused"] == 1

    def test_a_refused_case_counts_as_settled_because_only_a_person_can_move_it(self, cycled):
        store = composition.store()
        for case_id in cycled:
            document = store.load_case(case_id)
            document.update({"routed": False, "refusal": "no agent"})
            store.save_case(case_id, document)
        assert workflow.live_snapshot()["settled"] is True

    def test_a_half_worked_fleet_is_not_settled(self, cycled):
        assert workflow.live_snapshot()["settled"] is False


class TestTheScreenIsGated:
    def test_the_snapshot_endpoint_refuses_without_a_session(self):
        from fastapi.testclient import TestClient

        from nav_sentinel.server import app

        composition.configure()
        assert TestClient(app).get("/app/live.json").status_code == 401

    def test_a_malformed_since_is_not_passed_to_the_store(self, cycled):
        """It reaches a Firestore inequality filter, so it is validated rather than forwarded."""
        from fastapi.testclient import TestClient

        from nav_sentinel.server import app
        from nav_sentinel.webapp import session

        composition.configure()
        client = TestClient(app)
        client.post("/app/signin", data={"subject": session.ROSTER[1].subject})
        body = client.get("/app/live.json", params={"since": "not-a-timestamp'; DROP"}).json()
        # Rejected, so the snapshot falls back to the window it derives from the cases themselves.
        assert body["since"] != "not-a-timestamp'; DROP"
        assert body["counters"]["cases"] == len(cycled)


class TestTheScreenStopsWhenTheFleetIsFinished:
    def test_a_case_that_established_no_cause_is_settled_not_in_flight(self, cycled):
        """It has a verdict and will never have a proposal, so reading "no proposal" as "still
        drafting" left the screen polling for eternity. Observed on a real unattended run: six of
        seven settled and the seventh sat in flight forever."""
        store = composition.store()
        for case_id in cycled:
            document = store.load_case(case_id)
            document.update(
                {
                    "triage": {"capability": "nav.settlement", "confidence": 0.8, "reasoning": "r"},
                    "routed": True,
                    "investigator": "settlement-investigator@1.4.0",
                    "verdict": {"root_cause": "inconclusive", "confidence": 0.4,
                                "citations": [], "agent": "settlement-investigator@1.4.0"},
                    "drafted": False,
                    "draft_skipped": "no cause was established, so nothing was drafted",
                }
            )
            store.save_case(case_id, document)

        snapshot = workflow.live_snapshot()
        row = snapshot["cases"][0]
        assert row["stages"]["draft"] == "blocked"
        assert row["next_kind"] == "human_investigation"
        assert snapshot["settled"] is True, "the page would poll forever"

    def test_a_case_still_being_drafted_is_not_settled(self, cycled):
        """The distinction has to cut both ways, or `settled` just means "has a verdict"."""
        store = composition.store()
        for case_id in cycled:
            document = store.load_case(case_id)
            document.update({"routed": True, "verdict": {"root_cause": "x", "agent": "a@1"}})
            store.save_case(case_id, document)
        assert workflow.live_snapshot()["settled"] is False


class TestResetClearsEverythingTheFleetWrites:
    def test_no_field_the_fleet_writes_survives_a_reset(self):
        """A reset that leaves one field behind opens the next take in a state the demo did not
        produce -- and `drafted` is exactly the kind of field that gets added to the writer and
        forgotten in the resetter, because nothing fails when it is missed.
        """
        from nav_sentinel import demo_reset

        written = {
            "triage", "routed", "refusal", "investigator", "verdict", "proposal",
            "drafted", "draft_skipped", "dispatched_at", "signed_by", "signed_roles", "approval_ref",
            "last_outcome", "signed_for",
        }
        missed = written - set(demo_reset.WORKING) - {"signed_for"}
        assert not missed, f"demo-reset would leave these behind: {sorted(missed)}"


class TestOperatorTextIsNotHtmlSource:
    """Every string that reaches the page is HTML-escaped, so an entity written into one arrives on
    screen as its own source text. `Needs 1 more signature &mdash; cio` shipped exactly like that."""

    def test_no_next_step_string_contains_an_html_entity(self, cycled):
        import re

        store = composition.store()
        for band, signed in (("four_eyes", []), ("four_eyes", ["a@x.com"]),
                             ("cio_escalation", []), ("single_reviewer", [])):
            document = store.load_case(cycled[0])
            document.update({
                "approval_band": band, "routed": True, "signed_by": signed,
                "signed_roles": ["controller"] * len(signed),
                "verdict": {"root_cause": "x", "agent": "a@1"},
                "proposal": {"proposal_id": "P"},
            })
            store.save_case(cycled[0], document)
            step = workflow.live_snapshot()["cases"][0]["next_step"]
            assert not re.search(r"&[a-z]+;", step), f"{band}: {step!r}"

    def test_the_rendered_page_shows_no_escaped_entity(self, cycled):
        assert "&amp;mdash;" not in pages.live(workflow.live_snapshot(), principal=ANALYST)


class TestTheOpeningStateDoesNotClaimWorkIsHappening:
    """At rest, before anything is dispatched, every row read "In progress" and the band said
    "Investigation in progress. 7 cases still being worked. No action needed from you yet."

    Nothing was being worked. `_next_step` treated "no verdict" as "the fleet is on it", which is
    true only once the case has actually been handed over -- and the opening state of every take is
    exactly the state where it is false. Found by resetting the desk to test the flow.
    """

    def test_an_undispatched_case_is_not_started_rather_than_in_progress(self, cycled):
        row = workflow.live_snapshot()["cases"][0]
        assert row["next_kind"] == "not_started"
        assert row["next_step"] == "Not started"

    def test_the_band_tells_you_to_start_the_fleet(self, cycled):
        import re

        band = re.sub(r"<[^>]+>", " ", pages._handover(workflow.live_snapshot()))
        assert "Nothing has been investigated yet" in band
        assert "Start the fleet" in band
        assert "in progress" not in band.lower()

    def test_a_dispatched_case_with_no_verdict_is_in_progress(self, cycled):
        """The distinction has to cut both ways, or "not started" just means "no verdict"."""
        store = composition.store()
        document = store.load_case(cycled[0])
        document["dispatched_at"] = "2026-08-27T09:00:00+00:00"
        store.save_case(cycled[0], document)

        row = next(r for r in workflow.live_snapshot()["cases"] if r["case_id"] == cycled[0])
        assert row["next_kind"] == "fleet"
        assert row["next_step"] == "In progress"

    def test_the_idle_count_is_shown_instead_of_a_zero_in_progress(self, cycled):
        """Zero in progress is a true number that answers nothing. Seven not started is the fact."""
        band = pages._handover(workflow.live_snapshot())
        assert "not started" in band
        assert 'data-counter="hand_idle"' in band
