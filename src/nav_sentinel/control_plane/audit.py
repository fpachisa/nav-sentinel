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

from nav_sentinel.control_plane import policies, telemetry
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
    """
    with telemetry.span("nav_sentinel.exception_case", **facts.as_span_attributes()) as sp:
        trace_id = telemetry.current_trace_id()
        if trace_id:
            sp.set_attribute("nav.case.trace_id", trace_id)

        # The band is derived here, from the magnitude and its unit, rather than read off a
        # field the process already set.
        route = policies.approval_route(facts)
        sp.set_attribute("nav.case.approval_class", route.metadata["band"])

        yield sp, trace_id
