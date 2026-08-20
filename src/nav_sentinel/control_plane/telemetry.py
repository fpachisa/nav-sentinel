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
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
    SpanExportResult,
)

from nav_sentinel.config import settings

logger = logging.getLogger(__name__)
_configured = False

SERVICE_NAME = "nav-sentinel"
OTLP_ENDPOINT = "telemetry.googleapis.com"


class _CountingProcessor(SpanProcessor):
    """Counts spans as they end, so drops become visible.

    `_CountingExporter` counts what it is *handed*, never what was *created*, and
    `BatchProcessor.emit` drops on a full queue with only a warning and an internal metric -- the
    exporter is never told. So counting export failures alone left the original defect intact:
    measured, 50 spans created, 32 reaching the sink, zero failures, `flush()` True.

    This project has already had one span-queue overflow silently discard audit spans (see
    `gateway.py`, where a single `edgar.recent_filings` call produced 15,000 of them), so "the
    exporter reported no failures" is not the same claim as "the audit trail arrived".
    """

    def __init__(self, inner: SpanProcessor) -> None:
        self._inner = inner
        self.ended = 0

    def on_start(self, span, parent_context=None) -> None:
        self._inner.on_start(span, parent_context)

    def on_end(self, span) -> None:
        self.ended += 1
        self._inner.on_end(span)

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._inner.force_flush(timeout_millis)


class _CountingExporter(SpanExporter):
    """Wraps an exporter and remembers whether anything actually got out.

    Needed because `BatchSpanProcessor.force_flush` returns True unconditionally. In
    opentelemetry-sdk 1.42.1 it calls `_export(EXPORT_ALL)` -- which catches every exporter
    exception and logs it -- and then returns True regardless; there is also a `TODO: Fix force
    flush so the timeout is used` in the SDK, so its timeout argument is decorative. Measured:
    an exporter raising 504 DEADLINE_EXCEEDED still yields `force_flush() is True`, and 34 of 50
    spans dropped on queue overflow also yields True.

    So a flush result derived from `force_flush` alone cannot distinguish a delivered audit trail
    from a lost one, which is the only distinction this project needs it to make.
    """

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner
        self.exported = 0
        self.failures = 0

    def export(self, spans):
        try:
            result = self._inner.export(spans)
        except Exception:  # noqa: BLE001
            self.failures += 1
            raise
        if result is SpanExportResult.SUCCESS:
            self.exported += len(spans)
        else:
            self.failures += 1
        return result

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._inner.force_flush(timeout_millis)


#: The wrapper around whichever exporter is live, and where it sends. `None` until configured.
_exporter: _CountingExporter | None = None
_processor: _CountingProcessor | None = None
_target: str = "none"


def _otlp_exporter():
    """Export over OTLP to telemetry.googleapis.com.

    CloudTraceSpanExporter is deprecated; the supported path is the OTLP protocol against
    Google's telemetry endpoint, authenticated with application default credentials.
    """
    import google.auth
    import google.auth.transport.grpc
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

    global _exporter, _processor, _target
    _exporter, _processor, _target = None, None, "none"
    if s.enable_tracing and not console:
        try:
            _exporter = _CountingExporter(_otlp_exporter())
            _target = "cloud-trace"
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            # Deliberately broad: credentials, network and endpoint problems all surface
            # differently here, and none of them should stop a local run from producing a
            # reasoning trace. The fallback is console export, never silence.
            logger.warning("Cloud Trace exporter unavailable (%s); falling back to console", exc)

    if _exporter is None:
        # Console export keeps a local run auditable, but it is NOT Cloud Trace, and callers that
        # publish a trace id have to be able to tell the difference. Silently falling back and
        # still reporting success is how "the trace is in Cloud Trace" became unfalsifiable.
        _exporter = _CountingExporter(ConsoleSpanExporter())
        _target = "console"
    _processor = _CountingProcessor(BatchSpanProcessor(_exporter))
    provider.add_span_processor(_processor)

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


def export_target() -> str:
    """Where spans are actually going: `cloud-trace`, `console`, or `none`.

    A caller that publishes a trace id for a reviewer to open needs this, because console export
    succeeds perfectly while putting nothing in Cloud Trace.
    """
    return _target


def flush(timeout_millis: int = 12_000) -> bool:
    """Export everything buffered, now, and report whether it reached Cloud Trace.

    Cloud Run throttles a container's CPU to near zero as soon as a request finishes, so
    `BatchSpanProcessor`'s delayed flush runs with no CPU to run on. Measured on revision
    nav-sentinel-00002: the push returned at 02:02:28 and the exporter failed at 02:02:41 with
    DEADLINE_EXCEEDED, thirteen seconds after the response, having never got the spans out. So the
    caller flushes inside the request, while CPU is still allocated.

    The return value deliberately does **not** come from `force_flush`, which returns True even
    when every export raised and even when spans were dropped on queue overflow. It is the
    conjunction of four things: the flush completed, no export failed, **every span that ended
    was exported**, and the live exporter is the Cloud Trace one rather than the console fallback.

    The third conjunct is the one that took two attempts. Counting export failures does not
    detect a drop, because a dropped span is never handed to the exporter at all -- measured, 50
    spans created, 32 delivered, zero failures. Comparing cumulative counts also removes the need
    to snapshot failures around the flush, which was blind to a batch the background worker had
    already lost earlier in the same request.
    """
    provider = trace.get_tracer_provider()
    force_flush = getattr(provider, "force_flush", None)
    if force_flush is None or _exporter is None or _processor is None:  # never configured
        return False
    try:
        completed = bool(force_flush(timeout_millis))
    except Exception as exc:  # noqa: BLE001
        # Never let telemetry failure become request failure; the caller decides what an
        # unexported span means for its own contract.
        logger.warning("span flush failed (%s)", exc)
        return False

    lost = _processor.ended - _exporter.exported
    if lost > 0:
        logger.error(
            "audit spans lost: %d ended, %d exported (%d unaccounted)",
            _processor.ended, _exporter.exported, lost,
        )
    return (
        completed
        and _exporter.failures == 0
        and lost <= 0
        and _target == "cloud-trace"
    )


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
