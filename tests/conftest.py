"""Test harness setup.

Two things must hold for the suite to be trustworthy:

  * It must not export telemetry to the real Cloud Trace project. Without this, running
    tests writes spans into production telemetry and, absent credentials, falls back to a
    console exporter that dumps span JSON into test output and raises at interpreter
    shutdown.
  * The provider must be installed exactly once, before any span is emitted. OpenTelemetry
    ignores a second `set_tracer_provider`, so a per-test provider silently exports into the
    first one's exporter -- which reads as "no span emitted" in every later test.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nav_sentinel.control_plane import telemetry

_EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="session", autouse=True)
def _in_memory_tracing() -> InMemorySpanExporter:
    provider = TracerProvider(resource=Resource.create({"service.name": "nav-sentinel-test"}))
    provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    telemetry.use_provider(provider)
    return _EXPORTER


@pytest.fixture
def spans(_in_memory_tracing: InMemorySpanExporter) -> InMemorySpanExporter:
    """Per-test view of emitted spans, cleared on entry."""
    _in_memory_tracing.clear()
    return _in_memory_tracing
