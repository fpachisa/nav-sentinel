"""Stack compliance (S0a).

These tests guard the conditions that decide whether the entry qualifies at all, so they are
written to fail loudly rather than skip quietly. The live probe is marked so the offline suite
stays runnable, but `make verify` runs it.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nav_sentinel import compliance
from nav_sentinel.config import Settings, configure_sdk_environment, settings
from nav_sentinel.control_plane import telemetry


class TestLocationSplit:
    """Gemini 3.x is served only from 'global' on Vertex; every 3.x id returns 404 in a
    regional location. Model Armor is the reverse -- regional endpoint only. Conflating the
    two produces a 404 that reads like a permissions error, so the split is pinned here."""

    def test_model_location_is_global(self):
        assert settings().model_location == "global", (
            "Gemini 3.x resolves only at 'global'; a regional value 404s every model call"
        )

    def test_region_is_not_global(self):
        assert settings().region != "global", (
            "Model Armor, Cloud Run, Firestore and Pub/Sub have no 'global' location"
        )

    def test_model_armor_uses_the_regional_endpoint(self):
        s = settings()
        assert s.model_armor_endpoint == f"modelarmor.{s.region}.rep.googleapis.com"
        assert "global" not in s.model_armor_endpoint

    def test_armor_template_path_is_regional(self):
        from nav_sentinel.control_plane import model_armor

        assert f"/locations/{settings().region}/" in model_armor.template_path()


class TestModelTiers:
    def test_reasoning_model_is_gemini_3_5_or_newer(self):
        """The rules require Gemini 3.5+. Parse rather than trust the string."""
        m = settings().model_reasoning
        assert m.startswith("gemini-"), m
        version = float(m.split("-")[1])
        assert version >= 3.5, f"{m} is below the required Gemini 3.5 floor"

    def test_classify_model_is_gemini_3_5_or_newer(self):
        m = settings().model_classify
        version = float(m.split("-")[1])
        assert version >= 3.5, f"{m} is below the required Gemini 3.5 floor"


class TestPreflight:
    def test_vertex_transport_is_required(self, monkeypatch):
        bad = Settings(GOOGLE_GENAI_USE_VERTEXAI=False)
        monkeypatch.setattr(compliance, "settings", lambda: bad)
        with pytest.raises(compliance.ComplianceFailure, match="Vertex"):
            compliance.preflight()

    def test_gcloud_project_mismatch_is_fatal(self, monkeypatch):
        """Deploy scripts shell out to gcloud, which carries its own active configuration.
        A mismatch provisions the wrong project and surfaces much later as a permissions
        error against resources that were never created."""
        class FakeRun:
            stdout = "some-other-project\n"

        monkeypatch.setattr(compliance.subprocess, "run", lambda *a, **k: FakeRun())
        with pytest.raises(compliance.ComplianceFailure, match="gcloud active project"):
            compliance.preflight()

    def test_preflight_passes_in_a_correct_environment(self):
        notes = compliance.preflight()
        assert any("gcloud project matches" in n for n in notes)
        assert not any(n.startswith("WARNING") for n in notes), notes


class TestSdkEnvironmentBridge:
    """pydantic-settings reads `.env`; the Google SDKs read os.environ and never see it.
    Without the bridge the client is built with location=None and the failure surfaces as an
    opaque agent error rather than a configuration error."""

    def test_bridge_publishes_the_model_location_not_the_region(self):
        import os

        env = configure_sdk_environment()
        s = settings()
        assert env["GOOGLE_CLOUD_LOCATION"] == s.model_location == "global"
        assert env["GOOGLE_CLOUD_LOCATION"] != s.region
        assert os.environ["GOOGLE_CLOUD_PROJECT"] == s.project
        assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "true"


@pytest.fixture(scope="module")
def span_exporter():
    """One provider per process.

    OpenTelemetry's set_tracer_provider takes effect only on the first call; later calls are
    ignored. A per-test provider therefore silently exports into the first test's exporter,
    which reads as "no span emitted" in every test after the first.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "nav-sentinel-test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry.use_provider(provider)
    return exporter


@pytest.mark.live
class TestLiveProbe:
    """Requires Vertex AI credentials. This is the evidence, not a smoke test."""

    @pytest.mark.parametrize("tier", ["model_reasoning", "model_classify"])
    def test_returned_model_version_is_recorded_on_a_span(self, tier, span_exporter):
        span_exporter.clear()
        model_id = getattr(settings(), tier)
        probe = compliance.probe(model_id)

        assert probe.ok, f"{model_id} returned no model_version"
        assert probe.framework == "google-adk", "the model must be driven by a Google framework"
        assert probe.vertexai is True
        assert probe.reply, "the model returned no text"

        spans = [s for s in span_exporter.get_finished_spans()
                 if s.name == "nav_sentinel.compliance_probe"]
        assert spans, "no compliance span was emitted"
        attrs = spans[-1].attributes
        assert attrs["nav.compliance.model_version"] == probe.returned_version
        assert attrs["nav.compliance.model_requested"] == model_id
        assert attrs["nav.compliance.transport"] == "vertex-ai"
        assert attrs["nav.compliance.framework"] == "google-adk"
        assert attrs["nav.compliance.model_location"] == "global"

    def test_gemini_3x_is_unavailable_regionally(self, span_exporter):
        """Pins the finding that cost real diagnostic time: the Gemini 3.x family resolves
        only at 'global'. If Google later serves it regionally this test fails and the
        two-location split in config.py can be simplified."""
        import os

        from google import genai
        from google.genai import types

        client = genai.Client(vertexai=True, project=settings().project, location=settings().region)
        with pytest.raises(Exception) as exc:
            client.models.generate_content(
                model=settings().model_reasoning, contents="ping",
                config=types.GenerateContentConfig(max_output_tokens=8),
            )
        assert "404" in str(exc.value) or "NOT_FOUND" in str(exc.value), (
            f"expected 404 for {settings().model_reasoning} in {settings().region}; got {exc.value}"
        )
