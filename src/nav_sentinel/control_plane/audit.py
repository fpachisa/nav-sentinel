"""Case-level audit span.

One trace per exception case. Everything the fleet does to that case -- triage, registry
discovery, tool calls, screening verdicts, policy decisions, the drafted entry and its
approval routing -- hangs beneath a single root span.

That shape is chosen for the question an auditor actually asks: not "what did this agent do"
but "show me everything that happened to this NAV adjustment, and why".
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from opentelemetry import trace

from nav_sentinel.control_plane import telemetry
from nav_sentinel.domain.models import ExceptionCase


@contextmanager
def case_trace(case: ExceptionCase) -> Iterator[trace.Span]:
    """Open the root span for one exception case and stamp its trace id onto the case."""
    with telemetry.span(
        "nav_sentinel.exception_case",
        **{
            "nav.case.id": case.case_id,
            "nav.case.fund_id": case.fund_id,
            "nav.case.as_of": case.as_of.isoformat(),
            "nav.case.break_count": len(case.breaks),
            "nav.case.recurrence_key": case.recurrence_key,
        },
    ) as sp:
        case.trace_id = telemetry.current_trace_id()
        if case.trace_id:
            sp.set_attribute("nav.case.trace_id", case.trace_id)
        try:
            yield sp
        finally:
            sp.set_attribute("nav.case.status", case.status.value)
            if case.nav_impact_bps is not None:
                sp.set_attribute("nav.case.impact_bps", case.nav_impact_bps)
            if case.severity:
                sp.set_attribute("nav.case.severity", case.severity.value)
            if case.approval_class:
                sp.set_attribute("nav.case.approval_class", case.approval_class.value)
            if case.category:
                sp.set_attribute("nav.case.category", case.category.value)
