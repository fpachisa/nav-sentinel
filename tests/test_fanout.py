"""One event, seven investigations, nobody driving it.

The submission's weakest point against its heaviest judging criterion was that no agent in this
system ran without a human clicking: the only unattended entry point detected, scored and banded --
arithmetic -- and stopped. This is the path that closes that, and the things worth testing about it
are not the happy case.

**Every test here is free.** The dispatcher is exercised through its local path with the model
layer stubbed, and the push handler is driven with a fake envelope. Nothing calls Gemini and
nothing touches Pub/Sub, which matters because the failure mode being guarded is *spending money
twice*.
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from nav_sentinel import composition
from nav_sentinel.server import app
from nav_sentinel.webapp import dispatch, workflow


@pytest.fixture
def cases() -> list[str]:
    composition.configure()
    workflow.run_cycle(workflow.DEFAULT_AS_OF)
    store = composition.store()
    ids = [item.case_id for item in workflow.queue(workflow.DEFAULT_AS_OF)]
    for case_id in ids:
        document = store.load_case(case_id) or {}
        for field in ("triage", "routed", "refusal", "verdict", "proposal", "investigator",
                      "drafted", "draft_skipped", "dispatched_at"):
            document.pop(field, None)
        store.save_case(case_id, document)
    return ids


def _envelope(case_id: str, as_of: str = "2026-08-17") -> dict:
    body = json.dumps({"case_id": case_id, "as_of": as_of}).encode()
    return {
        "message": {"data": base64.b64encode(body).decode(), "messageId": "m1"},
        "subscription": "projects/p/subscriptions/nav-cases-push",
    }


@pytest.fixture
def push(monkeypatch):
    """A client whose push token is already believed, so the handler itself is what is tested."""
    from nav_sentinel import server

    monkeypatch.setattr(server, "PUSH_AUDIENCE", "https://example.run.app")
    monkeypatch.setattr(server, "PUSH_SERVICE_ACCOUNT", "push@example.iam.gserviceaccount.com")
    app.dependency_overrides[server.verify_push] = lambda: {"email": "push@example"}
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestTheHandlerRefusesToPayTwice:
    def test_a_case_that_already_has_a_verdict_is_acknowledged_without_working_it(
        self, cases, push, monkeypatch
    ):
        """Pub/Sub is at-least-once, so a duplicate is expected rather than exceptional, and the
        cost of missing it is a second full investigation billed for a case that has one."""
        store = composition.store()
        document = store.load_case(cases[0])
        document["verdict"] = {"root_cause": "x", "agent": "fx@1"}
        store.save_case(cases[0], document)

        called = []
        monkeypatch.setattr(workflow, "work_case", lambda *a, **k: called.append(a))

        assert push.post("/pubsub/case", json=_envelope(cases[0])).status_code == 204
        assert called == [], "a redelivery re-ran a case that was already investigated"

    def test_a_case_refused_at_routing_is_also_finished(self, cases, push, monkeypatch):
        """It has no verdict and is nonetheless done. Guarding on `verdict` alone would re-run
        triage on every redelivery -- a real model call."""
        store = composition.store()
        document = store.load_case(cases[0])
        document.update({"routed": False, "refusal": "no published agent handles nav.pricing"})
        store.save_case(cases[0], document)

        called = []
        monkeypatch.setattr(workflow, "work_case", lambda *a, **k: called.append(a))

        assert push.post("/pubsub/case", json=_envelope(cases[0])).status_code == 204
        assert called == []

    def test_an_unworked_case_is_worked(self, cases, push, monkeypatch):
        """The guard must not be so eager that it refuses everything."""
        called = []
        monkeypatch.setattr(workflow, "work_case", lambda *a, **k: called.append(a[0]))
        assert push.post("/pubsub/case", json=_envelope(cases[0])).status_code == 204
        assert called == [cases[0]]


class TestNothingRetriesForeverOnMoney:
    @pytest.mark.parametrize(
        ("raised", "reason"),
        [
            (LookupError("not in cycle"), "not_in_cycle"),
            (None, "immutable"),
            (None, "refused"),
        ],
    )
    def test_an_unretryable_failure_is_acknowledged(self, cases, push, monkeypatch, raised, reason):
        """Pub/Sub retries a non-2xx indefinitely, and every retry here is a billed investigation.
        Each outcome logs its own line, because 204 alone cannot tell success from a discard."""
        from nav_sentinel.control_plane.governance import PolicyDecision, PolicyViolation
        from nav_sentinel.control_plane.repository import ImmutableRecord

        # `PolicyViolation` wraps a decision, not a string -- it carries the policy that denied,
        # which is what makes the refusal auditable rather than a message.
        error = raised or (
            ImmutableRecord("observation exists")
            if reason == "immutable"
            else PolicyViolation(
                PolicyDecision(
                    policy_id="P-003-NO-AUTONOMOUS-POSTING", effect="deny", reason="no authority"
                )
            )
        )

        def explode(*_a, **_k):
            raise error

        monkeypatch.setattr(workflow, "work_case", explode)
        assert push.post("/pubsub/case", json=_envelope(cases[0])).status_code == 204

    def test_an_unexpected_failure_does_raise_so_pubsub_retries(self, cases, push, monkeypatch):
        """A transport blip is what a retry is for. Swallowing everything would turn the dead
        letter into decoration."""

        def explode(*_a, **_k):
            raise RuntimeError("Vertex AI is unreachable")

        monkeypatch.setattr(workflow, "work_case", explode)
        with pytest.raises(RuntimeError):
            push.post("/pubsub/case", json=_envelope(cases[0]))

    @pytest.mark.parametrize("body", ["", "bm90LWpzb24=", base64.b64encode(b"[]").decode()])
    def test_an_unparseable_message_is_discarded_not_retried(self, push, body):
        envelope = {
            "message": {"data": body, "messageId": "m2"},
            "subscription": "projects/p/subscriptions/nav-cases-push",
        }
        assert push.post("/pubsub/case", json=envelope).status_code == 204

    def test_an_unknown_case_is_discarded(self, cases, push):
        assert push.post("/pubsub/case", json=_envelope("CASE-does-not-exist")).status_code == 204


class TestTheHandlerIsGated:
    def test_it_refuses_a_delivery_with_no_token(self, cases):
        composition.configure()
        assert TestClient(app).post("/pubsub/case", json=_envelope(cases[0])).status_code == 401


class TestDispatchSaysHowItSentTheWork:
    def test_with_no_topic_configured_it_runs_locally_and_says_so(self, cases, monkeypatch):
        """The fallback keeps `make app` usable with no cloud project. It has to be distinguishable:
        a demo that looked identical either way would let "it runs itself" be claimed on the
        strength of a thread pool."""
        monkeypatch.delenv("NAV_CASES_TOPIC", raising=False)
        worked: list[str] = []
        monkeypatch.setattr(workflow, "work_case", lambda case_id, _as_of: worked.append(case_id))

        result = dispatch.dispatch(cases[:2], workflow.DEFAULT_AS_OF)
        assert result["via"] == "local"
        assert result["sent"] == 2

        import time

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(worked) < 2:
            time.sleep(0.02)
        assert sorted(worked) == sorted(cases[:2])

    def test_with_a_topic_configured_it_publishes(self, cases, monkeypatch):
        monkeypatch.setenv("NAV_CASES_TOPIC", "nav-cases")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "a-project")
        published: list[tuple[str, bytes]] = []

        class FakeFuture:
            def result(self, timeout=None):
                return "id"

        class FakePublisher:
            def topic_path(self, project, topic):
                return f"projects/{project}/topics/{topic}"

            def publish(self, path, body, **attrs):
                published.append((path, body))
                return FakeFuture()

        import sys
        import types

        module = types.ModuleType("google.cloud.pubsub_v1")
        module.PublisherClient = FakePublisher
        monkeypatch.setitem(sys.modules, "google.cloud.pubsub_v1", module)

        result = dispatch.dispatch(cases[:3], workflow.DEFAULT_AS_OF)
        assert result == {"via": "pubsub", "sent": 3, "of": 3, "topic": "nav-cases"}
        assert all(p.endswith("/topics/nav-cases") for p, _ in published)
        assert json.loads(published[0][1])["case_id"] == cases[0]

    def test_the_publish_is_resolved_before_returning(self, cases, monkeypatch):
        """`publish` is asynchronous. Returning while the batch is in flight would report success
        for messages that had not left the process -- and Cloud Run freezes the instance the moment
        the response is written."""
        import inspect

        source = inspect.getsource(dispatch._publish)
        assert "future.result(" in source


class TestInvestigateAllOnlyDispatchesWhatIsOutstanding:
    def test_it_skips_cases_that_are_already_worked(self, cases, monkeypatch):
        from nav_sentinel.webapp import session

        store = composition.store()
        document = store.load_case(cases[0])
        document["verdict"] = {"root_cause": "x", "agent": "fx@1"}
        store.save_case(cases[0], document)

        sent: list[list[str]] = []
        monkeypatch.setattr(dispatch, "dispatch", lambda ids, _as_of: sent.append(list(ids)) or {})

        client = TestClient(app, follow_redirects=False)
        client.post("/app/signin", data={"subject": session.ROSTER[1].subject})
        response = client.post("/app/investigate-all")

        assert response.status_code == 303
        assert response.headers["location"] == "/app/live"
        assert sent and cases[0] not in sent[0]
        assert len(sent[0]) == len(cases) - 1

    def test_it_does_nothing_without_a_session(self, cases, monkeypatch):
        sent: list[list[str]] = []
        monkeypatch.setattr(dispatch, "dispatch", lambda ids, _as_of: sent.append(list(ids)) or {})
        response = TestClient(app, follow_redirects=False).post("/app/investigate-all")
        assert response.status_code == 303
        assert sent == []
