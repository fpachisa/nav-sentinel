"""Case-level audit span.

One trace per exception case. Everything the fleet does to that case -- triage, registry
discovery, tool calls, screening verdicts, policy decisions, the drafted entry and its approval
routing -- hangs beneath a single root span.

That shape is chosen for the question an auditor actually asks: not "what did this agent do" but
"show me everything that happened to this adjustment, and why".
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace

from nav_sentinel.control_plane import gateway, telemetry
from nav_sentinel.control_plane.governance import CaseFacts


@contextmanager
def case_trace(facts: CaseFacts) -> Iterator[tuple[trace.Span, str | None]]:
    """Open the root span for one case, and yield it with the trace id.

    Takes `CaseFacts`, not a domain case. This function previously read eleven members of
    `ExceptionCase` -- including `fund_id`, the break collection, and three domain enums consumed
    as `.value` with no import at all -- which is what made the control plane unable to host any
    second process.

    Attributes come from `CaseFacts.as_span_attributes()`, owned by the control plane. A mapping
    supplied by the process would put the key names under process control and make the audit
    record non-uniform between processes, which is exactly what a shared governance log rules
    out.

    The trace id is yielded rather than written back onto the caller's object. Mutating it is how
    `trace_id` became a member the control plane had to know about in the first place.

    `CaseFacts` is an immutable snapshot taken at open, so the span records intake state. Call
    `close_case` with the terminal facts to record the outcome -- without it the trace answers
    "what did we know when this started" rather than "what happened to it".
    """
    with telemetry.span("nav_sentinel.exception_case", **facts.as_span_attributes()) as sp:
        trace_id = telemetry.current_trace_id()
        if trace_id:
            sp.set_attribute("nav.case.trace_id", trace_id)

        # Routed through the gateway rather than calling the policy directly, so the P-004
        # decision lands in the governance log. Calling policies.approval_route here recorded
        # the band on the span but left it out of the log the demo reads from.
        route = gateway.route_for_approval(facts)
        sp.set_attribute("nav.case.approval_class", route.metadata["band"])

        yield sp, trace_id


def close_case(span: trace.Span, facts: CaseFacts) -> None:
    """Stamp the terminal state of a case onto its root span.

    Separate from `case_trace` because `CaseFacts` is immutable: the facts at close are a
    different value from the facts at open, and the previous implementation only appeared to
    handle this by mutating a live domain object in a `finally` block.
    """
    span.set_attribute("nav.case.closed_status", facts.status)
    if facts.impact is not None:
        span.set_attribute("nav.case.closed_impact_value", str(facts.impact.value))
    if facts.severity:
        span.set_attribute("nav.case.closed_severity", facts.severity)
