"""The Cloud Run service's HTTP surface, and the deployment's security posture.

Auth on this service has two layers -- Cloud Run's IAM check and the handler's own OIDC
verification -- and only the second is testable offline. It exists precisely because the first is
a deployment-time flag that a later `gcloud run deploy` could quietly drop, so it is the one worth
pinning.

The deployment tests read `infra/deploy.sh` and the `Dockerfile` as text. That is a blunt
instrument, but the alternative is asserting nothing about the posture at all, and a flag silently
disappearing from a deploy script is exactly the failure these guard against.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nav_sentinel import server
from nav_sentinel.control_plane import gateway, identity, model_armor

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = (ROOT / "infra" / "deploy.sh").read_text()
DOCKERFILE = (ROOT / "Dockerfile").read_text()


@pytest.fixture
def client():
    return TestClient(server.app)


PUSH_SA = "nav-pubsub-push@example.iam.gserviceaccount.com"
PUSH_AUD = "https://nav-sentinel.example.run.app"


def _configure_push(monkeypatch, *, email=PUSH_SA, email_verified=True):
    """Set the service up the way a real deployment does, then accept one token.

    Earlier versions of these tests set both push variables to "" and relied on the handler
    skipping verification -- encoding the fail-open as expected behaviour. The handler now refuses
    when either is unset, so tests must configure it like production.
    """
    from google.oauth2 import id_token

    monkeypatch.setattr(server, "PUSH_SERVICE_ACCOUNT", PUSH_SA)
    monkeypatch.setattr(server, "PUSH_AUDIENCE", PUSH_AUD)
    monkeypatch.setattr(
        id_token,
        "verify_oauth2_token",
        lambda *a, **k: {"email": email, "email_verified": email_verified},
    )


def _envelope(payload: dict | None = None, *, raw: str | None = None) -> dict:
    data = raw if raw is not None else base64.b64encode(json.dumps(payload or {}).encode()).decode()
    return {"message": {"data": data, "messageId": "1"}, "subscription": "s"}


class TestHealthEndpoints:
    def test_health_says_nothing_about_the_fund(self, client):
        """A health endpoint is the one thing reachable before auth is fully wired, so it must not
        leak the fund, the fixtures or the registry."""
        body = client.get("/health").json()
        assert body == {"status": "ok", "service": "nav-sentinel"}

    def test_liveness_is_not_served_at_the_reserved_healthz_path(self, client):
        """On Cloud Run the Google Frontend answers `/healthz` before the container sees it, so a
        liveness probe there is unreachable however well it works locally. Measured on revision
        nav-sentinel-00002: `/healthz` returns Google's HTML 404, `/health` returns this app's
        body. This test fails if anyone renames the endpoint back to the conventional path."""
        assert [r for r in server.app.routes if getattr(r, "path", None) == "/healthz"] == []

    def test_readyz_reports_the_hosted_processes(self, client):
        body = client.get("/readyz").json()
        assert body["status"] == "ready"
        assert "nav" in body["processes"]
        assert body["agents"] >= 7

    def test_interactive_docs_are_disabled(self, client):
        """FastAPI serves /docs by default. A service handling fund data should not."""
        assert client.get("/docs").status_code == 404
        assert server.app.docs_url is None
        assert server.app.redoc_url is None

    def test_the_route_inventory_is_not_published(self, client):
        """Turning off /docs while leaving /openapi.json served still hands out every route."""
        assert client.get("/openapi.json").status_code == 404


class TestThePushEndpointAuthenticates:
    def test_a_request_with_no_token_is_rejected(self, client):
        assert client.post("/pubsub/exceptions", json=_envelope()).status_code == 401

    def test_a_non_bearer_authorization_header_is_rejected(self, client):
        response = client.post(
            "/pubsub/exceptions", json=_envelope(), headers={"Authorization": "Basic abc"}
        )
        assert response.status_code == 401

    def test_an_unverifiable_token_is_rejected(self, client, monkeypatch):
        monkeypatch.setattr(server, "PUSH_SERVICE_ACCOUNT", PUSH_SA)
        monkeypatch.setattr(server, "PUSH_AUDIENCE", PUSH_AUD)
        response = client.post(
            "/pubsub/exceptions", json=_envelope(), headers={"Authorization": "Bearer not.a.jwt"}
        )
        assert response.status_code == 401

    @pytest.mark.parametrize("missing", ["audience", "service_account", "both"])
    def test_an_unconfigured_service_refuses_rather_than_accepting_anything(
        self, client, monkeypatch, missing
    ):
        """`audience=PUSH_AUDIENCE or None` meant an empty variable disabled audience checking
        outright -- google.auth documents None as "the audience is not verified" -- and an empty
        service account skipped the identity check. With both unset the endpoint accepted any
        Google-signed token carrying email_verified. deploy.sh sets the audience only in a
        *second* revision, so every deployment passed through that state."""
        from google.oauth2 import id_token

        monkeypatch.setattr(
            server, "PUSH_AUDIENCE", "" if missing in ("audience", "both") else PUSH_AUD
        )
        monkeypatch.setattr(
            server,
            "PUSH_SERVICE_ACCOUNT",
            "" if missing in ("service_account", "both") else PUSH_SA,
        )
        monkeypatch.setattr(
            id_token,
            "verify_oauth2_token",
            lambda *a, **k: {"email": "anyone@gmail.com", "email_verified": True},
        )
        response = client.post(
            "/pubsub/exceptions", json=_envelope(), headers={"Authorization": "Bearer x"}
        )
        assert response.status_code == 500

    def test_the_audience_is_passed_to_verification_verbatim(self, client, monkeypatch):
        """Guards the `or None` regression specifically: the audience must reach google.auth."""
        from google.oauth2 import id_token

        seen = {}
        monkeypatch.setattr(server, "PUSH_SERVICE_ACCOUNT", PUSH_SA)
        monkeypatch.setattr(server, "PUSH_AUDIENCE", PUSH_AUD)

        def capture(_token, _request, audience=None, **_kw):
            seen["audience"] = audience
            return {"email": PUSH_SA, "email_verified": True}

        monkeypatch.setattr(id_token, "verify_oauth2_token", capture)
        client.post("/pubsub/exceptions", json=_envelope(), headers={"Authorization": "Bearer x"})
        assert seen["audience"] == PUSH_AUD

    def test_a_valid_token_from_the_wrong_identity_is_rejected(self, monkeypatch):
        """A Google-signed token from the wrong service account is still the wrong identity, and
        Cloud Run's IAM check would have let it through if that identity held run.invoker."""
        _configure_push(monkeypatch, email="someone-else@example.iam.gserviceaccount.com")
        response = TestClient(server.app).post(
            "/pubsub/exceptions", json=_envelope(), headers={"Authorization": "Bearer x"}
        )
        assert response.status_code == 403

    def test_an_unverified_email_claim_is_rejected(self, monkeypatch):
        _configure_push(monkeypatch, email_verified=False)
        response = TestClient(server.app).post(
            "/pubsub/exceptions", json=_envelope(), headers={"Authorization": "Bearer x"}
        )
        assert response.status_code == 403


class TestUndeliverableMessagesAreNotRetriedForever:
    """Pub/Sub retries any non-2xx indefinitely, so a message this service can never process must
    be acknowledged rather than rejected."""

    @pytest.fixture(autouse=True)
    def _accept_the_push_token(self, monkeypatch):
        _configure_push(monkeypatch)

    @pytest.mark.parametrize(
        "envelope",
        [
            _envelope(raw="not-base64!!"),
            _envelope(raw=base64.b64encode(b"not json").decode()),
            _envelope({}),  # no as_of
            _envelope({"as_of": "not-a-date"}),
        ],
    )
    def test_an_unprocessable_message_is_acknowledged(self, envelope):
        response = TestClient(server.app).post(
            "/pubsub/exceptions", json=envelope, headers={"Authorization": "Bearer x"}
        )
        assert response.status_code == 204

    @pytest.mark.parametrize(
        "body",
        [
            "[1, 2]",              # valid JSON, not subscriptable
            '"hello"',
            "5",
            "true",
            "null",
            '{"as_of": 5}',        # subscriptable, but fromisoformat needs a str
            '{"as_of": null}',
            '{"as_of": ["2026-08-17"]}',
        ],
    )
    def test_a_malformed_payload_is_acknowledged_not_retried(self, body):
        """Each of these returned 500 and was redelivered forever -- the same defect class the
        204 design exists to prevent, feeding a dead-letter topic nothing reads. The parse guard
        caught only binascii/Unicode/JSONDecodeError, and every case here raises TypeError."""
        envelope = _envelope(raw=base64.b64encode(body.encode()).decode())
        response = TestClient(server.app).post(
            "/pubsub/exceptions", json=envelope, headers={"Authorization": "Bearer x"}
        )
        assert response.status_code == 204, body

    def test_a_valid_but_unknown_cycle_is_acknowledged_not_retried(self):
        """`2020-01-01` parses fine and has no NAV record, so the cycle raised, the response was a
        500, and Pub/Sub redelivered it forever -- the exact outcome the 204 design exists to
        avoid. The previous tests only covered dates that fail to *parse*."""
        response = TestClient(server.app).post(
            "/pubsub/exceptions",
            json=_envelope({"as_of": "2020-01-01"}),
            headers={"Authorization": "Bearer x"},
        )
        assert response.status_code == 204


class TestTheDeploymentPosture:
    def test_the_service_is_not_publicly_invokable(self):
        """Checking that `--allow-unauthenticated` is absent is not the property: the script could
        grant `allUsers` the invoker role with a separate `add-iam-policy-binding`, which it
        already uses that exact command shape for elsewhere."""
        assert "--no-allow-unauthenticated" in DEPLOY
        assert "--allow-unauthenticated" not in DEPLOY.replace("--no-allow-unauthenticated", "")
        for principal in ("allUsers", "allAuthenticatedUsers"):
            assert principal not in DEPLOY, f"{principal} is granted access"

    def test_it_runs_as_a_dedicated_service_account(self):
        assert "--service-account" in DEPLOY
        assert "nav-runtime@" in DEPLOY
        assert "compute@developer" not in DEPLOY, "the default compute SA is not least privilege"

    def test_push_uses_a_separate_identity_with_an_audience(self):
        """A leaked push token should be able to invoke this service and nothing else."""
        assert "nav-pubsub-push@" in DEPLOY
        assert "--push-auth-service-account" in DEPLOY
        assert "--push-auth-token-audience" in DEPLOY

    def test_the_runtime_holds_no_publish_permission(self):
        """The service consumes exceptions; it does not produce them.

        Asserting the *string* `roles/pubsub.publisher` is absent stopped working once the
        dead-letter policy required granting it to Pub/Sub's own service agent -- a different
        principal entirely. The property is about who receives it."""
        for line in DEPLOY.splitlines():
            if "roles/pubsub.publisher" in line:
                assert "gcp-sa-pubsub" in line or "PUBSUB_AGENT" in line, line
                assert "nav-runtime" not in line, line

    def test_the_dead_letter_topic_is_not_the_source_topic(self):
        """It was, so a message failing five attempts was republished to the topic it came from
        and redelivered by the same subscription -- an unbounded loop spending Gemini and Model
        Armor calls on every turn."""
        assert 'DLQ_TOPIC="nav-exceptions-dlq"' in DEPLOY
        assert '--dead-letter-topic "$TOPIC"' not in DEPLOY
        assert '--dead-letter-topic "$DLQ_TOPIC"' in DEPLOY

    def test_the_dead_letter_policy_is_not_silently_optional(self):
        """The create was `2>/dev/null || <create with no dead-letter policy>`, so the broken form
        failed silently and the fallback -- which retries forever -- is what actually ran."""
        assert "--max-delivery-attempts 5 2>/dev/null" not in DEPLOY

    def test_redeploy_preserves_unacknowledged_messages(self):
        """Delete-and-recreate discards the backlog, and on an exception queue those are the
        audit-bearing messages."""
        assert "subscriptions delete nav-exceptions-push" not in DEPLOY
        assert "subscriptions update nav-exceptions-push" in DEPLOY

    def test_deploy_refuses_the_wrong_project(self):
        assert "gcloud config get-value project" in DEPLOY

    def test_the_image_runs_as_a_non_root_user(self):
        assert "USER nav" in DOCKERFILE
        assert "useradd" in DOCKERFILE

    def test_no_secret_material_is_baked_into_the_image(self):
        """Directives only. Checking the whole file matched the comment that says there are no
        service-account keys in it, which is the opposite of a finding."""
        directives = "\n".join(
            line for line in DOCKERFILE.splitlines() if line.strip() and not line.startswith("#")
        )
        for forbidden in (".env", "credentials", "GOOGLE_APPLICATION_CREDENTIALS", ".json"):
            assert forbidden not in directives, f"{forbidden!r} appears in a Dockerfile directive"
        ignore = (ROOT / ".dockerignore").read_text()
        assert ".env" in ignore
        assert ".git" in ignore

    def test_the_image_honours_the_port_cloud_run_assigns(self):
        assert "${PORT}" in DOCKERFILE

    def test_teardown_keeps_what_is_free_at_rest(self):
        teardown = (ROOT / "infra" / "teardown.sh").read_text()
        assert "--dry-run" in teardown
        for kept in ("service-accounts delete", "topics delete", "databases delete"):
            assert kept not in teardown, f"teardown removes {kept}, making deploy unreproducible"


class TestSpansAreExportedBeforeTheResponse:
    """Cloud Run throttles CPU when a request ends, so a background flush never runs. These pin
    the in-request flush that replaced it, and the fact that its failure cannot fail the request."""

    def test_flush_reports_false_when_no_provider_can_flush(self, monkeypatch):
        from opentelemetry import trace as ot

        from nav_sentinel.control_plane import telemetry

        monkeypatch.setattr(ot, "get_tracer_provider", object)
        assert telemetry.flush() is False

    def test_a_failing_exporter_does_not_fail_the_request(self, monkeypatch):
        from opentelemetry import trace as ot

        from nav_sentinel.control_plane import telemetry

        class Exploding:
            def force_flush(self, timeout_millis=None):
                raise RuntimeError("DEADLINE_EXCEEDED")

        monkeypatch.setattr(ot, "get_tracer_provider", Exploding)
        assert telemetry.flush() is False

    def test_the_cycle_response_states_whether_its_spans_got_out(self, monkeypatch):
        """The response advertises trace ids; a reviewer must be able to tell when they are not
        actually in Cloud Trace rather than following a dead link."""
        from nav_sentinel.control_plane import telemetry

        monkeypatch.setattr(telemetry, "flush", lambda *a, **k: False)
        body = TestClient(server.app).get("/cycle/2026-08-17").json()
        assert body["spans_exported"] is False

    def test_the_push_handler_flushes_its_audit_spans(self, monkeypatch):
        """Acking a message whose audit span was dropped leaves the work unevidenced.

        Named for what it actually checks: the flush happened during the request. An earlier
        version claimed to assert ordering while comparing a single-element list against itself.
        """
        from nav_sentinel.control_plane import telemetry

        calls: list[str] = []
        _configure_push(monkeypatch)
        monkeypatch.setattr(telemetry, "flush", lambda *a, **k: (calls.append("flush"), True)[1])

        response = TestClient(server.app).post(
            "/pubsub/exceptions",
            json=_envelope({"as_of": "2026-08-17"}),
            headers={"Authorization": "Bearer x"},
        )
        assert response.status_code == 204
        assert calls == ["flush"], "the handler acknowledged without flushing its audit spans"


class TestTheServiceConfiguresItself:
    """Two shipped entry points once relied on something else having called `configure()`, and
    both were broken by a manifest relocation without failing a test. The suite's own fixture
    configures the registry, so `/readyz` passing proves nothing about the app's wiring -- these
    exercise the app's lifespan directly."""

    def test_startup_registers_the_processes(self, monkeypatch):
        """Runs the *real* `configure()` through the app's own lifespan after a reset.

        The earlier version monkeypatched `configure` to a no-op and asserted it had been called,
        so it would still have passed in the failure mode it was written for -- a manifest
        relocation making the real `configure()` raise, which has already shipped twice.

        `NAV_APPROVALS=memory` is set deliberately. Without it the real lifespan builds a Firestore
        client, which calls `google.auth.default()`, so this test required live credentials and
        `make test` no longer ran with the network unreachable. The approvals backend is not what
        this test is about; the test below covers the deployed default.
        """
        from nav_sentinel import composition
        from nav_sentinel.control_plane import packs

        monkeypatch.setenv("NAV_APPROVALS", "memory")
        composition.reset()
        assert not packs.registered(), "reset left processes registered; the test proves nothing"
        try:
            with TestClient(server.app) as fresh:
                body = fresh.get("/readyz").json()
                assert body["status"] == "ready"
                assert "nav" in body["processes"]
        finally:
            composition.configure()

    def test_the_deployed_default_approvals_backend_is_firestore(self, monkeypatch):
        """In-memory approvals in production would make four-eyes approval a per-instance
        fiction that vanishes when Cloud Run scales to zero."""
        monkeypatch.delenv("NAV_APPROVALS", raising=False)
        seen: list[str] = []
        monkeypatch.setattr(
            server.composition, "configure", lambda **kw: seen.append(kw["approvals_backend"])
        )
        monkeypatch.setattr(server.telemetry, "configure_tracing", lambda **kw: None)
        with TestClient(server.app):
            pass
        assert seen == ["firestore"]
        assert "NAV_APPROVALS=firestore" in DEPLOY, "the deployed service must use durable approvals"


class TestTheSelfTestProvesReachabilityHonestly:
    """The self-test is the S7a evidence, so it must not be able to report healthy when the
    managed services are unreachable or when the filter denies nothing."""

    def _run(
        self,
        monkeypatch,
        *,
        vertex_ok=True,
        armor_raises=None,
        injection_verdict="MATCH_FOUND",
        matched=(model_armor.PRIMARY_FILTER,),
        injection_blocked=True,
        flush_ok=True,
    ):
        from nav_sentinel import compliance
        from nav_sentinel.control_plane import telemetry

        class Probe:
            requested, returned_version, location = "gemini-3.7-flash", "gemini-3.7-flash", "global"
            trace_id = "abc"
            ok = vertex_ok

        async def fake_probe(_model_id):
            if not vertex_ok:
                raise RuntimeError("PermissionDenied")
            return Probe()

        def fake_admit(text, *, source_uri=None):  # noqa: ARG001
            if armor_raises:
                raise armor_raises("unreachable")
            if "IGNORE ALL PREVIOUS" in text:
                if injection_blocked:
                    raise model_armor.ContentBlocked(
                        model_armor.ArmorVerdict(
                            blocked=True, verdict=injection_verdict, matched_filters=tuple(matched)
                        ),
                        source_uri,
                    )
                return text
            return text

        monkeypatch.setattr(compliance, "probe_async", fake_probe)
        monkeypatch.setattr(server.gateway, "admit_untrusted_content", fake_admit)
        monkeypatch.setattr(telemetry, "flush", lambda *a, **k: flush_ok)
        monkeypatch.setattr(telemetry, "export_target", lambda: "cloud-trace")
        return TestClient(server.app).get("/selftest").json()

    def test_healthy_when_both_services_answer_and_the_filter_denies(self, monkeypatch):
        body = self._run(monkeypatch)
        assert body["healthy"] is True
        assert body["model_armor"]["injection_denied"] is True

    def test_unhealthy_when_vertex_is_unreachable(self, monkeypatch):
        body = self._run(monkeypatch, vertex_ok=False)
        assert body["healthy"] is False
        assert body["vertex_gemini"]["reachable"] is False

    def test_unhealthy_when_model_armor_is_unreachable(self, monkeypatch):
        body = self._run(monkeypatch, armor_raises=ConnectionError)
        assert body["healthy"] is False
        assert body["model_armor"]["reachable"] is False

    @pytest.mark.parametrize(
        "verdict",
        ["screening_unavailable", "invocation_incomplete", "primary_filter_absent",
         "primary_filter_skipped", "too_large_to_screen"],
    )
    def test_a_failure_to_screen_is_never_reported_as_a_denial(self, monkeypatch, verdict):
        """`screen()` funnels six fail-closed reasons through one exception type. Recording only
        the exception class name scored a 503 on the injection call as a successful denial, so a
        Model Armor outage during the probe reported healthy: true."""
        body = self._run(monkeypatch, injection_verdict=verdict, matched=())
        assert body["model_armor"]["injection_denied"] is False
        assert body["model_armor"]["denial_verdict"] == verdict
        assert body["healthy"] is False

    def test_a_match_on_some_other_filter_is_not_a_prompt_injection_denial(self, monkeypatch):
        """A MATCH_FOUND on, say, the CSAM filter says nothing about injection detection."""
        body = self._run(monkeypatch, injection_verdict="MATCH_FOUND", matched=("csam",))
        assert body["model_armor"]["injection_denied"] is False
        assert body["healthy"] is False

    def test_unhealthy_when_spans_did_not_reach_cloud_trace(self, monkeypatch):
        """The fourth conjunct of `healthy` was never exercised."""
        body = self._run(monkeypatch, flush_ok=False)
        assert body["spans_exported"] is False
        assert body["healthy"] is False

    def test_the_probe_does_not_leave_governance_records_behind(self, monkeypatch):
        """`admit_untrusted_content` records ALLOW/DENY against a real published agent, so an
        unguarded self-test let any caller manufacture governance-log entries reading as a genuine
        injection attempt on SEC content.

        The stub must therefore *record a decision*. An earlier version stubbed it with a function
        that recorded nothing, so the log was never polluted and the test passed with
        `restore_decision_log` replaced by a complete no-op -- it could not detect the removal of
        the fix it is named for.
        """
        from nav_sentinel.control_plane import gateway as gw
        from nav_sentinel.control_plane import model_armor
        from nav_sentinel.control_plane.policies import Effect, PolicyDecision

        def polluting_admit(text, *, source_uri=None):
            gw._record(
                PolicyDecision(
                    effect=Effect.ALLOW,
                    policy_id="P-005-UNTRUSTED-INGEST",
                    reason="fabricated by the probe",
                    agent_ref="corporate-actions-investigator@2.1.0",
                    resource=source_uri or "",
                )
            )
            if "IGNORE ALL PREVIOUS" in text:
                raise model_armor.ContentBlocked(
                    model_armor.ArmorVerdict(
                        blocked=True,
                        verdict="MATCH_FOUND",
                        matched_filters=(model_armor.PRIMARY_FILTER,),
                    ),
                    source_uri,
                )
            return text

        gw.clear_decision_log()
        with identity.acting_as("triage-agent"):
            gateway.call_tool("registry.coverage")
        before = gw.decision_log()
        assert before, "nothing in the log, so the test could not detect pollution"

        monkeypatch.setattr(server.gateway, "admit_untrusted_content", polluting_admit)
        monkeypatch.setattr(server.telemetry, "flush", lambda *a, **k: True)
        from nav_sentinel import compliance

        class Probe:
            requested = returned_version = "gemini-3.7-flash"
            location, trace_id, ok = "global", "abc", True

        async def fake_probe(_m):
            return Probe()

        monkeypatch.setattr(compliance, "probe_async", fake_probe)

        body = TestClient(server.app).get("/selftest").json()
        assert body["model_armor"]["injection_denied"] is True, "the probe never ran"
        assert gw.decision_log() == before, "the self-test left fabricated governance records"

    def test_the_probe_source_is_not_a_real_regulator_domain(self):
        """The probe once cited https://www.sec.gov/selftest, so a fabricated record named the SEC
        as the source of an injection attempt."""
        assert "sec.gov" not in server._PROBE_SOURCE

    def test_unhealthy_when_the_filter_admits_the_injection(self, monkeypatch):
        """A self-test that only checked the benign path would pass against a filter that never
        denies anything, which is the failure mode worth catching."""
        body = self._run(monkeypatch, injection_blocked=False)
        assert body["healthy"] is False
        assert body["model_armor"]["injection_denied"] is False

    def test_the_probe_payload_is_unmistakably_an_override_attempt(self):
        text = server._PROBE_INJECTION.lower()
        assert "ignore all previous instructions" in text
        assert "without human review" in text


class _Sink:
    """A span exporter that succeeds and remembers what it was given."""

    def __init__(self) -> None:
        self.spans: list = []

    def export(self, spans):
        from opentelemetry.sdk.trace.export import SpanExportResult

        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


class TestTheFlushSignalCannotLie:
    """`BatchSpanProcessor.force_flush` returns True unconditionally -- it calls `_export`, which
    catches every exporter exception, then returns True regardless; its timeout argument carries a
    `TODO: Fix force flush so the timeout is used` in the SDK. So a flush result derived from it
    cannot distinguish a delivered audit trail from a lost one, which is the only thing this
    project needs it to distinguish."""

    @pytest.fixture
    def install(self, monkeypatch):
        """Wire a provider whose exporter we control, and put the globals back afterwards.

        A fixture rather than a plain helper because the previous version assigned
        `telemetry._exporter` / `_target` with no cleanup, so the values leaked to the end of the
        session and later tests' flush results depended on class ordering -- the same
        unfalsifiability shape, inside the suite.
        """
        from opentelemetry import trace as ot
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        from nav_sentinel.control_plane import telemetry

        def _install(exporter, target, *, spans=1, **batch):
            monkeypatch.setattr(telemetry, "_exporter", telemetry._CountingExporter(exporter))
            monkeypatch.setattr(telemetry, "_target", target)
            monkeypatch.setattr(
                telemetry,
                "_processor",
                telemetry._CountingProcessor(BatchSpanProcessor(telemetry._exporter, **batch)),
            )
            provider = TracerProvider()
            provider.add_span_processor(telemetry._processor)
            tracer = provider.get_tracer("t")
            for i in range(spans):
                with tracer.start_as_current_span(f"s{i}"):
                    pass
            monkeypatch.setattr(ot, "get_tracer_provider", lambda: provider)
            return provider

        return _install

    def test_the_raw_sdk_signal_really_is_unconditional(self):
        """The premise, pinned. If a future SDK fixes this, the wrapper becomes redundant rather
        than wrong -- but this test tells us which world we are in."""
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

        class Boom(SpanExporter):
            def export(self, spans):
                raise RuntimeError("504 DEADLINE_EXCEEDED")

            def shutdown(self):
                pass

        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(Boom()))
        with provider.get_tracer("t").start_as_current_span("s"):
            pass
        assert provider.force_flush(2000) is True

    def test_a_failing_export_reports_false(self, install):
        from opentelemetry.sdk.trace.export import SpanExporter

        from nav_sentinel.control_plane import telemetry

        class Boom(SpanExporter):
            def export(self, spans):
                raise RuntimeError("504 DEADLINE_EXCEEDED")

            def shutdown(self):
                pass

        install(Boom(), "cloud-trace")
        assert telemetry.flush(2000) is False

    def test_a_successful_export_to_cloud_trace_reports_true(self, install):
        from nav_sentinel.control_plane import telemetry

        install(_Sink(), "cloud-trace")
        assert telemetry.flush(2000) is True

    def test_the_console_fallback_is_not_reported_as_cloud_trace(self, install):
        """Console export succeeds perfectly while putting nothing in Cloud Trace, and S7a exists
        to prove spans reach Cloud Trace. Reporting success here is how that became
        unfalsifiable."""
        from nav_sentinel.control_plane import telemetry

        install(_Sink(), "console")
        assert telemetry.flush(2000) is False
        assert telemetry.export_target() == "console"

    def test_spans_dropped_on_a_full_queue_are_detected(self, install):
        """The defect the first fix missed. A dropped span is never handed to the exporter, so
        counting export failures cannot see it: measured, 50 created, 32 delivered, zero failures,
        flush() True. The signal now compares spans ended against spans exported."""
        from nav_sentinel.control_plane import telemetry

        sink = _Sink()
        install(sink, "cloud-trace", spans=50, max_queue_size=8, max_export_batch_size=4)
        result = telemetry.flush(4000)
        assert telemetry._processor.ended == 50
        assert telemetry._exporter.exported < 50, "no drop occurred; the test proves nothing"
        assert result is False, "spans were lost and flush() still reported success"


class TestTheGovernanceLogSurvivesConcurrency:
    """The log was a module-global list on a service that clears it per request, deployed at Cloud
    Run's default concurrency. Concurrent requests destroyed each other's audit records: one cycle
    serially recorded 28 decisions while eight concurrent cycles reported 80, 28, 54, 132, 184,
    106, 158 and 210."""

    def test_concurrent_cycles_each_see_only_their_own_decisions(self):
        import asyncio
        from datetime import date

        from nav_sentinel.control_plane import gateway
        from nav_sentinel.pipeline import cycle_runner

        def one() -> int:
            gateway.clear_decision_log()
            return cycle_runner.run(date(2026, 8, 17))["decisions"]

        expected = one()
        assert expected > 0

        async def eight():
            return await asyncio.gather(*[asyncio.to_thread(one) for _ in range(8)])

        assert asyncio.run(eight()) == [expected] * 8

    def test_a_snapshot_can_be_restored(self):
        """What keeps `/selftest` from leaving fabricated governance records behind."""
        from nav_sentinel.control_plane import gateway, identity

        gateway.clear_decision_log()
        with identity.acting_as("triage-agent"):
            gateway.call_tool("registry.coverage")
        snapshot = gateway.decision_log()
        assert snapshot

        with identity.acting_as("triage-agent"):
            gateway.call_tool("registry.coverage")
        assert len(gateway.decision_log()) > len(snapshot)

        gateway.restore_decision_log(snapshot)
        assert gateway.decision_log() == snapshot
