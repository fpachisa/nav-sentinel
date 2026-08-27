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
        """The distinction has to cut both ways, or "not started" just means "no verdict".

        Relative to now, not a fixed date. It was `2026-08-27T09:00:00`, which was recent when
        written and became older than the stall threshold the next day -- a test that passes on the
        day it is written and fails afterwards is worse than no test, because the failure looks like
        a regression in the code.
        """
        from nav_sentinel.control_plane.observations import utcnow

        store = composition.store()
        document = store.load_case(cycled[0])
        document["dispatched_at"] = utcnow().isoformat()
        store.save_case(cycled[0], document)

        row = next(r for r in workflow.live_snapshot()["cases"] if r["case_id"] == cycled[0])
        assert row["next_kind"] == "fleet"
        assert row["next_step"] == "In progress"

    def test_the_idle_count_is_shown_instead_of_a_zero_in_progress(self, cycled):
        """Zero in progress is a true number that answers nothing. Seven not started is the fact."""
        band = pages._handover(workflow.live_snapshot())
        assert "not started" in band
        assert 'data-counter="hand_idle"' in band


class TestTheNextStepCountsWhatIsActuallyOutstanding:
    """"Needs 1 more signature" says a signature is already on the case.

    So a queue of untouched CIO escalations claimed to be half approved, and the one number an
    analyst most needs to trust on that screen -- how far from signed a case is -- was the number
    that lied. "More" is only correct once one has been given.
    """

    WORKED = {"routed": True, "verdict": {"agent": "a@1"}, "proposal": {"proposal_id": "P"}}

    def _step(self, band: str, signed: list[str]) -> str:
        return workflow._next_step(
            {**self.WORKED, "approval_band": band, "signed_by": signed}
        )[1]

    def test_one_signature_from_one_role_names_the_role(self):
        assert self._step("cio_escalation", []) == "CIO to sign"

    def test_one_signature_from_any_of_several_roles_does_not_list_them(self):
        """Naming three roles is noise when any one of them will do."""
        assert self._step("single_reviewer", []) == "Signature required"
        assert self._step("auto_clear", []) == "Signature required"

    def test_two_signatures_with_none_given_says_two(self):
        assert self._step("four_eyes", []) == "2 signatures required"

    def test_only_a_partly_signed_case_says_more(self):
        step = self._step("four_eyes", ["first@x.example"])
        assert step.startswith("Needs 1 more signature")
        assert "CIO or Controller" in step

    def test_nothing_unsigned_ever_claims_a_signature_exists(self):
        """The property, across every band: with no signatures, the wording cannot say "more"."""
        from nav_sentinel.control_plane.approvals import BAND_REQUIREMENTS

        for band in BAND_REQUIREMENTS:
            step = self._step(band.value, [])
            assert "more" not in step.lower(), f"{band.value}: {step!r}"

    def test_the_wording_holds_no_html_entity(self):
        import re

        for band in ("cio_escalation", "four_eyes", "single_reviewer"):
            for signed in ([], ["a@x.example"]):
                step = self._step(band, signed)
                assert not re.search(r"&[a-z]+;", step), f"{band}/{len(signed)}: {step!r}"


class TestResetOpensOnTheCurrentSchema:
    """Clearing the worked fields is not enough to open a take cleanly.

    Detection output survives a reset by design -- it is not work an analyst did -- so a field added
    to the *detected* case never reached the deployed documents. `currency`, which is the only thing
    telling two cash breaks apart, shipped and the screen kept showing the same title twice, because
    `Investigate all` dispatches existing cases and does not re-detect.
    """

    def test_reset_re_detects_so_new_fields_appear(self):
        from nav_sentinel import demo_reset

        composition.configure()
        store = composition.store()
        workflow.run_cycle(workflow.DEFAULT_AS_OF)
        ids = [i.case_id for i in workflow.queue(workflow.DEFAULT_AS_OF)]

        # A document written by an older schema: the currency was never stored.
        cash = next(c for c in ids if "cash" in c)
        document = store.load_case(cash)
        document.pop("currency", None)
        document["verdict"] = {"root_cause": "x", "agent": "a@1"}
        store.save_case(cash, document)
        assert "currency" not in store.load_case(cash)

        demo_reset.reset()

        after = store.load_case(cash)
        assert after.get("currency"), "reset left a case that detection can no longer describe"
        assert "verdict" not in after, "reset did not clear the work"

    def test_no_two_titles_collide_after_a_reset(self):
        from nav_sentinel import demo_reset
        from nav_sentinel.webapp.pages import describe

        composition.configure()
        demo_reset.reset()
        store = composition.store()
        titles = [
            describe(store.load_case(i.case_id) or {})
            for i in workflow.queue(workflow.DEFAULT_AS_OF)
        ]
        assert len(set(titles)) == len(titles), sorted(t for t in titles if titles.count(t) > 1)


class TestAStalledCaseSaysSo:
    """A lost delivery is not retried until the subscription's ack deadline expires, which is
    deliberately longer than one investigation. Observed live: a case sat "In progress" from
    02:31:50 to 02:36:50 -- five minutes of a screen reporting work that was not happening."""

    def _step(self, seconds_ago: int) -> tuple[str, str]:
        from datetime import timedelta

        from nav_sentinel.control_plane.observations import utcnow

        return workflow._next_step(
            {"routed": True, "dispatched_at": (utcnow() - timedelta(seconds=seconds_ago)).isoformat()}
        )

    def test_a_recently_dispatched_case_is_in_progress(self):
        assert self._step(20) == ("fleet", "In progress")

    def test_a_case_with_no_progress_past_the_threshold_says_so(self):
        kind, step = self._step(workflow.STALL_AFTER_SECONDS + 30)
        assert kind == "stalled"
        assert "retry" in step.lower()

    def test_the_threshold_sits_between_one_investigation_and_the_ack_deadline(self):
        """Longer than a cold investigation so a slow case is not libelled; shorter than the 300s
        ack deadline so the operator learns before the automatic retry rather than after it."""
        assert 74 < workflow.STALL_AFTER_SECONDS < 300

    def test_an_unreadable_timestamp_does_not_declare_a_stall(self):
        kind, _ = workflow._next_step({"routed": True, "dispatched_at": "not-a-time"})
        assert kind == "fleet"

    def test_the_band_offers_the_retry(self):
        import re

        snapshot = {
            "handover": {"sign": 0, "human_investigation": 0, "fleet": 0,
                         "not_started": 0, "stalled": 2},
            "settled": False,
        }
        band = re.sub(r"<[^>]+>", " ", pages._handover(snapshot))
        assert "stopped making progress" in band
        assert "run it again" in band


class TestTheNextStepDoesNotChangeItsMind:
    """It announced a conclusion, then withdrew it.

    "verdict, but no proposal" is *no cause established* when the fleet decided not to draft, and
    is also the seconds between the verdict landing and the draft landing. So a case read "Cause
    not established — needs an analyst" and then changed to "Signature required" -- and an operator
    watching a queue do that learns to distrust the column. A conclusion that arrives before the
    work is finished is worse than no conclusion.
    """

    #: Every document state a case actually passes through, in order, as `_work` persists them.
    def _sequence(self) -> list[tuple[str, dict]]:
        from nav_sentinel.control_plane.observations import utcnow

        now = utcnow().isoformat()
        base = {"dispatched_at": now, "approval_band": "four_eyes"}
        verdict = {"root_cause": "x", "confidence": 0.9, "agent": "fx@1"}
        return [
            ("detected", {"approval_band": "four_eyes"}),
            ("dispatched", {**base}),
            ("triaged", {**base, "triage": {"capability": "nav.fx_rate"}}),
            ("routed", {**base, "triage": {}, "routed": True, "investigator": "fx@1"}),
            ("verdict", {**base, "routed": True, "verdict": verdict}),
            ("drafted", {**base, "routed": True, "verdict": verdict,
                         "proposal": {"proposal_id": "P"}}),
        ]

    TERMINAL_WORDS = ("needs an analyst", "signature", "signatures", "release to the ledger")

    def test_nothing_terminal_is_claimed_before_the_draft_lands(self):
        steps = [(label, workflow._next_step(d)[1]) for label, d in self._sequence()]
        for label, step in steps[:-1]:
            assert not any(w in step.lower() for w in self.TERMINAL_WORDS), (
                f"at '{label}' the column already claimed an outcome: {step!r}"
            )
        assert steps[-1][1] == "2 signatures required"

    def test_the_column_takes_exactly_two_values_before_the_answer(self):
        """One for "you have not started it" and one for "it is being worked". Any third is a
        state that will be contradicted."""
        values = {workflow._next_step(d)[1] for _label, d in self._sequence()[:-1]}
        assert values == {"Not started", "In progress"}, sorted(values)

    def test_a_deliberate_no_cause_still_reads_as_terminal(self):
        """The distinction must cut both ways, or "In progress" becomes the answer to everything."""
        from nav_sentinel.control_plane.observations import utcnow

        kind, step = workflow._next_step({
            "dispatched_at": utcnow().isoformat(),
            "routed": True,
            "verdict": {"root_cause": "inconclusive", "agent": "a@1"},
            "drafted": False,
        })
        assert kind == "human_investigation"
        assert "Cause not established" in step


class TestOneStageSpinsAtATime:
    """A row of static dots gives no sign the fleet is alive.

    The stage being worked is derived rather than recorded: the stages run in order, so the first
    unfinished one is the one in hand. A field written per stage would be a second source of truth
    for something the sequence already determines -- and the worker that knows is on another
    instance.
    """

    T = {"capability": "nav.fx_rate", "confidence": 0.9, "reasoning": "r"}
    V = {"root_cause": "x", "agent": "a@1"}

    def _stages(self, cycled, fields: dict) -> dict:
        from nav_sentinel.control_plane.observations import utcnow

        store = composition.store()
        document = store.load_case(cycled[0])
        document.update({"dispatched_at": utcnow().isoformat(), **fields})
        store.save_case(cycled[0], document)
        return next(
            r for r in workflow.live_snapshot()["cases"] if r["case_id"] == cycled[0]
        )["stages"]

    def test_exactly_one_stage_runs_while_the_case_is_in_flight(self, cycled):
        for fields in (
            {},
            {"triage": self.T},
            {"triage": self.T, "routed": True},
            {"triage": self.T, "routed": True, "verdict": self.V},
        ):
            stages = self._stages(cycled, fields)
            running = [k for k, v in stages.items() if v == "running"]
            assert len(running) == 1, f"{fields.keys()} -> {stages}"

    def test_the_spinner_is_on_the_first_unfinished_stage(self, cycled):
        stages = self._stages(cycled, {"triage": self.T, "routed": True})
        assert stages["triage"] == "done"
        assert stages["routing"] == "done"
        assert stages["investigation"] == "running"
        assert stages["draft"] == "pending"

    def test_a_finished_case_has_no_spinner(self, cycled):
        stages = self._stages(
            cycled,
            {"triage": self.T, "routed": True, "verdict": self.V,
             "proposal": {"proposal_id": "P"}},
        )
        assert "running" not in stages.values()

    def test_an_undispatched_case_has_no_spinner(self, cycled):
        """Nothing is working on it, so nothing should look like it is."""
        store = composition.store()
        document = store.load_case(cycled[0])
        for field in ("dispatched_at", "triage", "routed", "verdict", "proposal"):
            document.pop(field, None)
        store.save_case(cycled[0], document)
        stages = next(
            r for r in workflow.live_snapshot()["cases"] if r["case_id"] == cycled[0]
        )["stages"]
        assert set(stages.values()) == {"pending"}

    def test_a_refused_case_has_no_spinner(self, cycled):
        stages = self._stages(cycled, {"triage": self.T, "routed": False, "refusal": "none"})
        assert "running" not in stages.values()
        assert stages["routing"] == "refused"

    def test_an_empty_result_still_counts_as_a_finished_stage(self, cycled):
        """Presence, not truthiness. A triage that classified nothing is not a triage that never
        ran -- the same conflation of "no answer" and "not asked" the next-step column had."""
        stages = self._stages(cycled, {"triage": {}})
        assert stages["triage"] == "done"

    def test_the_running_state_is_styled(self):
        assert "[data-s=running]" in pages.CSS
        assert "animation:spin" in pages.CSS
        assert "@keyframes pop" in pages.CSS


class TestStalenessIsMeasuredFromNow:
    """One fixture pinned `dispatched_at` to a calendar date. It was minutes old when written and
    hours old the next day, which put it past the stall threshold -- so the suite reported a
    regression in code that had not changed.

    I first wrote this as a grep for literal timestamps in this file and it flagged two legitimate
    fixtures: one uses `2020-01-01` deliberately to make a counting window wide, the other asserts
    a window *equals* a specific value. Neither rots. A guard that cannot tell intent apart from
    error is a guard that gets weakened the first time it is wrong, so this asserts the property the
    stall logic actually depends on instead.
    """

    def test_a_stamp_made_now_reads_as_no_elapsed_time(self):
        from nav_sentinel.control_plane.observations import utcnow

        assert workflow._stalled_for(utcnow().isoformat()) < 2

    def test_elapsed_time_is_measured_against_the_current_clock(self):
        from datetime import timedelta

        from nav_sentinel.control_plane.observations import utcnow

        elapsed = workflow._stalled_for((utcnow() - timedelta(seconds=400)).isoformat())
        assert 395 < elapsed < 405

    def test_a_fixed_past_date_would_always_read_as_stalled(self):
        """Which is why a fixture meaning "just dispatched" has to be built from now."""
        assert workflow._stalled_for("2020-01-01T00:00:00+00:00") > workflow.STALL_AFTER_SECONDS
