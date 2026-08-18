"""OpenTelemetry tracing, exported to Cloud Trace.

The reasoning chain is not debug output here -- it is the audit artefact. A fund
administrator asked why a NAV was adjusted must be able to answer with evidence, so every
span carries the agent's version-pinned registry reference, the policy decisions taken, the
evidence cited and the materiality that drove routing.

That reframing is deliberate: telemetry designed for auditors happens to be excellent
telemetry for engineers, but the reverse is not reliably true.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Iterator

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from nav_sentinel.config import settings

logger = logging.getLogger(__name__)
_configured = False

SERVICE_NAME = "nav-sentinel"
OTLP_ENDPOINT = "telemetry.googleapis.com"


def _otlp_exporter():
    """Export over OTLP to telemetry.googleapis.com.

    CloudTraceSpanExporter is deprecated; the supported path is the OTLP protocol against
    Google's telemetry endpoint, authenticated with application default credentials.
    """
    import google.auth
    import google.auth.transport.grpc  # noqa: F401  (registers the transport)
    import google.auth.transport.requests
    import grpc
    from google.auth.transport.grpc import AuthMetadataPlugin
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    credentials, _ = google.auth.default()
    auth_plugin = AuthMetadataPlugin(
        credentials=credentials, request=google.auth.transport.requests.Request()
    )
    channel_creds = grpc.composite_channel_credentials(
        grpc.ssl_channel_credentials(),
        grpc.metadata_call_credentials(auth_plugin),
    )
    return OTLPSpanExporter(endpoint=OTLP_ENDPOINT, credentials=channel_creds)


def configure_tracing(*, console: bool = False) -> None:
    """Idempotent. Falls back to console export when Cloud Trace is unavailable so that a
    local run still produces a full reasoning trace."""
    global _configured
    if _configured:
        return

    s = settings()
    resource = Resource.create(
        {
            "service.name": SERVICE_NAME,
            "service.version": "0.1.0",
            "cloud.provider": "gcp",
            "cloud.account.id": s.project,
            "cloud.region": s.region,
            # Required by telemetry.googleapis.com; the export is rejected without it.
            "gcp.project_id": s.project,
        }
    )
    provider = TracerProvider(resource=resource)

    exported = False
    if s.enable_tracing and not console:
        try:
            provider.add_span_processor(BatchSpanProcessor(_otlp_exporter()))
            exported = True
        except Exception as exc:  # pragma: no cover - depends on ambient credentials
            logger.warning("Cloud Trace exporter unavailable (%s); falling back to console", exc)

    if not exported:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _configured = True


def use_provider(provider: TracerProvider) -> None:
    """Install a specific provider and mark tracing configured.

    Exists so tests can attach an in-memory exporter and assert on emitted spans. The
    acceptance criteria require span attributes to be verified by test rather than read off
    a console, which is only possible if the provider can be substituted.
    """
    global _configured
    trace.set_tracer_provider(provider)
    _configured = True


def tracer() -> trace.Tracer:
    configure_tracing()
    return trace.get_tracer(SERVICE_NAME)


def _flatten(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, str)):
        return value
    return str(value)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(name) as sp:
        for k, v in attributes.items():
            if v is not None:
                sp.set_attribute(k, _flatten(v))
        yield sp


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    if not ctx or not ctx.trace_id:
        return None
    return format(ctx.trace_id, "032x")


def record_policy_decision(attributes: dict[str, str]) -> None:
    """Attach a policy decision to the *enclosing* span as an event.

    Decisions are events, not spans. A separate span per decision would fragment the
    audit trail into orphan traces, and the question an auditor asks is "show me
    everything that happened to this case", which requires one trace per case.
    """
    sp = trace.get_current_span()
    if sp is None or not sp.is_recording():
        return
    sp.add_event("policy_decision", attributes=attributes)


def record_evidence(sp: trace.Span, source: str, summary: str, *, trusted: bool,
                    armor_verdict: str | None = None) -> None:
    """Attach one evidence item to the trace as an event, so the chain of support for a
    conclusion is reconstructable from the trace alone."""
    sp.add_event(
        "evidence",
        attributes={
            "nav.evidence.source": source,
            "nav.evidence.summary": summary[:400],
            "nav.evidence.trusted": trusted,
            **({"nav.evidence.armor_verdict": armor_verdict} if armor_verdict else {}),
        },
    )
