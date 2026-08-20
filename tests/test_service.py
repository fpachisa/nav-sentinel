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

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = (ROOT / "infra" / "deploy.sh").read_text()
DOCKERFILE = (ROOT / "Dockerfile").read_text()


@pytest.fixture
def client():
    return TestClient(server.app)


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

    def test_an_unverifiable_token_is_rejected(self, client):
        response = client.post(
            "/pubsub/exceptions", json=_envelope(), headers={"Authorization": "Bearer not.a.jwt"}
        )
        assert response.status_code == 401

    def test_a_valid_token_from_the_wrong_identity_is_rejected(self, monkeypatch):
        """A Google-signed token from the wrong service account is still the wrong identity, and
        Cloud Run's IAM check would have let it through if that identity held run.invoker."""
        monkeypatch.setattr(
            server, "PUSH_SERVICE_ACCOUNT", "expected@example.iam.gserviceaccount.com"
        )
        monkeypatch.setattr(server, "PUSH_AUDIENCE", "https://nav-sentinel.example.run.app")

        from google.oauth2 import id_token

        monkeypatch.setattr(
            id_token,
            "verify_oauth2_token",
            lambda *a, **k: {
                "email": "someone-else@example.iam.gserviceaccount.com",
                "email_verified": True,
            },
        )
        response = TestClient(server.app).post(
            "/pubsub/exceptions", json=_envelope(), headers={"Authorization": "Bearer x"}
        )
        assert response.status_code == 403

    def test_an_unverified_email_claim_is_rejected(self, monkeypatch):
        from google.oauth2 import id_token

        monkeypatch.setattr(server, "PUSH_SERVICE_ACCOUNT", "")
        monkeypatch.setattr(
            id_token,
            "verify_oauth2_token",
            lambda *a, **k: {"email": "anyone@example.com", "email_verified": False},
        )
        response = TestClient(server.app).post(
            "/pubsub/exceptions", json=_envelope(), headers={"Authorization": "Bearer x"}
        )
        assert response.status_code == 403


class TestUndeliverableMessagesAreNotRetriedForever:
    """Pub/Sub retries any non-2xx indefinitely, so a message this service can never process must
    be acknowledged rather than rejected."""

    @pytest.fixture(autouse=True)
    def _accept_any_token(self, monkeypatch):
        from google.oauth2 import id_token

        monkeypatch.setattr(server, "PUSH_SERVICE_ACCOUNT", "")
        monkeypatch.setattr(server, "PUSH_AUDIENCE", "")
        monkeypatch.setattr(
            id_token,
            "verify_oauth2_token",
            lambda *a, **k: {"email": "push@example.com", "email_verified": True},
        )

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


class TestTheDeploymentPosture:
    def test_the_service_is_not_publicly_invokable(self):
        assert "--no-allow-unauthenticated" in DEPLOY
        assert "--allow-unauthenticated" not in DEPLOY.replace("--no-allow-unauthenticated", "")

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
        """The service consumes exceptions; it does not produce them."""
        assert "roles/pubsub.publisher" not in DEPLOY

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

    def test_the_push_handler_flushes_before_acknowledging(self, monkeypatch):
        """Acking a message whose audit span was dropped leaves the work unevidenced."""
        from google.oauth2 import id_token

        from nav_sentinel.control_plane import telemetry

        order: list[str] = []
        monkeypatch.setattr(server, "PUSH_SERVICE_ACCOUNT", "")
        monkeypatch.setattr(server, "PUSH_AUDIENCE", "")
        monkeypatch.setattr(
            id_token,
            "verify_oauth2_token",
            lambda *a, **k: {"email": "push@example.com", "email_verified": True},
        )
        monkeypatch.setattr(telemetry, "flush", lambda *a, **k: (order.append("flush"), True)[1])

        response = TestClient(server.app).post(
            "/pubsub/exceptions",
            json=_envelope({"as_of": "2026-08-17"}),
            headers={"Authorization": "Bearer x"},
        )
        assert response.status_code == 204
        assert order == ["flush"], "the handler acknowledged without flushing its audit spans"


class TestTheServiceConfiguresItself:
    """Two shipped entry points once relied on something else having called `configure()`, and
    both were broken by a manifest relocation without failing a test. The suite's own fixture
    configures the registry, so `/readyz` passing proves nothing about the app's wiring -- these
    exercise the app's lifespan directly."""

    def test_startup_registers_the_processes(self, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr(
            server.composition, "configure", lambda **kw: called.append(kw.get("approvals_backend"))
        )
        monkeypatch.setattr(server.telemetry, "configure_tracing", lambda **kw: None)
        with TestClient(server.app):
            pass
        assert called, "the service started without registering any process pack"

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

    def _run(self, monkeypatch, *, vertex_ok=True, armor_raises=None, injection_blocked=True):
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
                    raise server.gateway.ContentBlocked("blocked")
                return text
            return text

        monkeypatch.setattr(compliance, "probe_async", fake_probe)
        monkeypatch.setattr(server.gateway, "admit_untrusted_content", fake_admit)
        monkeypatch.setattr(telemetry, "flush", lambda *a, **k: True)
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
