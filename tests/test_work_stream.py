"""Working a case reports itself as it happens, and one click is one investigation.

Three things were wrong with the button that runs the fleet. It stayed enabled after being
clicked, so a second click started a second set of model calls against the same case. The page
then sat completely still for the several seconds those calls take, which is indistinguishable
from a hang. And every result appeared at once at the end, so nothing on screen showed that
triage, routing, investigation and drafting are four separate authorised steps.

No model is called here. The three agent entry points are stubbed, because what is under test is
the sequencing, the persistence and the failure handling -- not what Gemini says.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from nav_sentinel import composition
from nav_sentinel.server import app
from nav_sentinel.webapp import session, workflow

CONTROLLER = "j.laurent@merian.example"


def _cite(observation_id: str) -> SimpleNamespace:
    return SimpleNamespace(observation_id=observation_id)


@pytest.fixture
def stubbed(monkeypatch):
    """Stand in for the three model calls, in the shapes the pipeline consumes."""

    async def classify(case, agent):
        return SimpleNamespace(
            capability="nav.fx_rate",
            confidence=0.91,
            reasoning="market value differs while quantity agrees",
            overridden_from=None,
            classified=True,
        )

    async def investigate(brief, agent, trace_id=None):
        verdict = SimpleNamespace(
            root_cause="a stale USD rate was applied",
            confidence=0.93,
            citations=[_cite("OBS-1")],
            unresolved="",
            asserts_a_cause=True,
        )
        return verdict, SimpleNamespace(as_mapping=lambda: {})

    async def draft(case, verdict, agent, trace_id=None):
        return SimpleNamespace(
            proposal_id="PROP-test",
            outcome=SimpleNamespace(value="correcting_entry"),
            rationale="revalue at the published rate",
            expected_residual="0.00",
            requires=SimpleNamespace(value="four_eyes"),
            lines=[],
            quantity_lines=[],
        )

    monkeypatch.setattr(workflow.triage, "classify", classify)
    monkeypatch.setattr(workflow, "investigate", investigate)
    monkeypatch.setattr(workflow.remediation, "draft", draft)


@pytest.fixture
def client() -> TestClient:
    composition.configure()
    client = TestClient(app, follow_redirects=False)
    client.post("/app/signin", data={"subject": CONTROLLER})
    return client


@pytest.fixture
def case_id(client) -> str:
    client.post("/app/cycle")
    items = workflow.queue(workflow.DEFAULT_AS_OF)
    case = next(i for i in items if i.band == "four_eyes")
    document = composition.store().load_case(case.case_id)
    for field in ("verdict", "triage", "proposal", "signed_by", "approval_ref"):
        document.pop(field, None)
    composition.store().save_case(case.case_id, document)
    return case.case_id


def _stream(client: TestClient, case_id: str) -> list[dict]:
    response = client.post(f"/app/case/{case_id}/work/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in response.text.splitlines() if line]


class TestTheStagesArriveInOrder:
    def test_every_stage_reports_running_before_it_reports_done(self, stubbed, client, case_id):
        events = _stream(client, case_id)
        order = [(e.get("stage"), e["state"]) for e in events]
        assert order == [
            ("triage", "running"),
            ("triage", "done"),
            ("routing", "running"),
            ("routing", "done"),
            ("investigation", "running"),
            ("investigation", "done"),
            ("proposal", "running"),
            ("proposal", "done"),
            (None, "finished"),
        ], order

    def test_each_finished_stage_carries_the_html_for_its_own_section(self, stubbed, client, case_id):
        done = {e["stage"]: e for e in _stream(client, case_id) if e["state"] == "done"}
        assert "Triage" in done["triage"]["html"]
        assert "Established cause" in done["investigation"]["html"]
        assert "Proposed correction" in done["proposal"]["html"]
        # Routing succeeded, so it renders nothing of its own -- only a refusal has a panel.
        assert done["routing"]["html"] == ""

    def test_the_last_line_carries_the_approval_rail(self, stubbed, client, case_id):
        finished = _stream(client, case_id)[-1]
        assert finished["state"] == "finished"
        assert "Approval" in finished["rail"]
        assert "Run the fleet" not in finished["rail"]


class TestTheStreamReportsWhatIsAlreadyStored:
    def test_a_stage_is_persisted_before_its_line_goes_out(self, stubbed, client, case_id):
        """Otherwise the reveal is theatre: the screen would show a cause that a refresh loses.

        Driven through the generator directly so the assertion can run *between* stages.
        """
        store = composition.store()
        seen: list[str] = []
        for event in workflow.work_case_events(case_id, workflow.DEFAULT_AS_OF):
            if event.state != "done":
                continue
            seen.append(event.stage)
            stored = store.load_case(case_id)
            if event.stage == "triage":
                assert stored.get("triage"), "triage was announced before it was saved"
            if event.stage == "investigation":
                assert stored.get("verdict"), "the cause was announced before it was saved"
            if event.stage == "proposal":
                assert stored.get("proposal"), "the draft was announced before it was saved"
        assert seen == ["triage", "routing", "investigation", "proposal"]

    def test_the_non_streaming_post_drives_the_same_generator(self, stubbed, client, case_id):
        """The no-JavaScript path must not be a second implementation that quietly rots."""
        assert client.post(f"/app/case/{case_id}/work").status_code == 303
        stored = composition.store().load_case(case_id)
        assert stored["triage"] and stored["verdict"] and stored["proposal"]


class TestFailuresDoNotLeaveTheScreenSpinning:
    def test_a_failure_mid_stream_is_reported_as_a_line_not_a_truncated_body(
        self, stubbed, client, case_id, monkeypatch
    ):
        async def explode(brief, agent, trace_id=None):
            raise RuntimeError("Vertex AI said no")

        monkeypatch.setattr(workflow, "investigate", explode)
        events = _stream(client, case_id)

        assert events[-1]["state"] == "failed", events[-1]
        assert events[-1]["detail"] == "RuntimeError"
        # And the stages that did complete are still on record.
        assert composition.store().load_case(case_id).get("triage")

    def test_abandoning_the_stream_still_persists_the_governance_decisions(
        self, stubbed, client, case_id, monkeypatch
    ):
        """A closed tab closes the stream, which throws `GeneratorExit` into the generator. The
        record of what the gateway allowed and refused on the way must survive that: a trail that
        only persists on the happy path is not a trail.

        The first version of this test asserted the *verdict* was stored, which `patch()` had
        already done before the abandon -- so it passed with the `finally` deleted, and its name
        described a property it never checked.
        """
        from nav_sentinel.control_plane.governance import PolicyDecision

        decision = PolicyDecision(
            policy_id="P-001-TOOL-ALLOWLIST",
            effect="allow",
            reason="ecb_fx.rate_on is in the allowlist",
            subject_id=case_id,
        )
        # The stubs bypass the gateway, so there would otherwise be no decisions to lose and the
        # assertion could not fail. This makes the state the test claims to be about exist.
        monkeypatch.setattr(workflow.gateway, "decisions_since", lambda _case: [decision])

        store = composition.store()
        # A count, not an emptiness check: case ids are content-derived and the store is shared
        # across this module, so an earlier test in the file may already have recorded decisions
        # against this case. The property is that abandoning the stream *adds* the ones it saw.
        before = len(store.decisions_for(case_id))

        events = workflow.work_case_events(case_id, workflow.DEFAULT_AS_OF)
        for event in events:
            if event.stage == "investigation" and event.state == "done":
                break
        events.close()  # the consumer walks away mid-investigation

        assert len(store.decisions_for(case_id)) > before, (
            "the gateway's decisions were lost when the client disconnected"
        )


class TestOneClickIsOneInvestigation:
    def test_the_button_disables_itself_on_submit(self, client, case_id):
        page = client.get(f"/app/case/{case_id}").text
        assert "b.disabled=true" in page, "a second click starts a second set of model calls"
        assert 'id="work-form"' in page

    def test_the_stream_refuses_without_a_session(self, case_id):
        composition.configure()
        anonymous = TestClient(app, follow_redirects=False)
        assert anonymous.post(f"/app/case/{case_id}/work/stream").status_code == 401

    def test_it_is_a_post_so_a_prefetch_cannot_spend_money(self):
        """`EventSource` would have forced a GET. A GET that calls Gemini is one a link preview,
        a crawler or a browser prefetch can trigger."""
        composition.configure()
        paths = {
            (r.path, tuple(sorted(r.methods or ())))
            for r in getattr(
                next(
                    (x for x in app.routes if type(x).__name__ == "_IncludedRouter"), None
                ).original_router,
                "routes",
                [],
            )
        }
        stream = [m for p, m in paths if p.endswith("/work/stream")]
        assert stream and "GET" not in stream[0], stream
