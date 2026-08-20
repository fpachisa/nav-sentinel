"""`python -m nav_sentinel.pipeline.investigate_cli` -- one case, investigated by the fleet.

Separate from `make demo` on purpose. The demo is the deterministic spine and must keep running
with the network unreachable; this calls a real model, so it cannot. Keeping them apart is what
lets the offline guarantee stay unqualified.

This is the shot the video needs: a break, an agent reasoning about it against real published
reference data, and a verdict whose every cited number is traceable to a recorded tool call.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from nav_sentinel import composition
from nav_sentinel.agents import investigator
from nav_sentinel.control_plane import audit, gateway
from nav_sentinel.domain import materiality
from nav_sentinel.domain.models import BreakCategory
from nav_sentinel.pipeline import cycle_runner
from nav_sentinel.registry import discover
from nav_sentinel.tools import books_and_records as bnr

#: The stale-rate scenario. Named rather than "the first case", so the shot is reproducible.
DEFAULT_ISIN = "US0378331005"


def main() -> int:
    composition.configure()
    console = Console()
    as_of = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 8, 17)
    isin = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ISIN

    cases = cycle_runner.detect(as_of)
    matching = [c for c in cases if any(b.isin == isin for b in c.breaks)]
    if not matching:
        console.print(f"[red]no case on {as_of} involves {isin}[/red]")
        console.print(f"available: {sorted({b.isin for c in cases for b in c.breaks if b.isin})}")
        return 1
    case = matching[0]

    # Materiality and the band come from the control plane, exactly as in the deterministic cycle.
    # The investigator explains the break; it does not decide who signs off on it.
    custodian_nav = bnr.nav_record("custodian", cycle_runner.FUND, as_of)
    materiality.score(case, custodian_nav, cycle_runner._fixture_rates(as_of))

    # Triage is S1.4. Until then the capability is *asserted* here, not classified -- and that is
    # printed on screen rather than left in a source comment, because run against another ISIN this
    # target silently mislabelled: a settlement timing difference came out as nav.fx_rate at
    # 285bps critical and was handed to the FX investigator, whose manifest has no trades access.
    case.category = BreakCategory.FX_RATE
    facts = case.to_facts()

    agent = discover.discover_for_capability(facts.capability)
    if agent is None:
        console.print(f"[red]no authorised investigator for {facts.capability}[/red]")
        return 1

    console.print(
        Panel(
            f"[bold]{case.case_id}[/bold]\n"
            f"fund {case.fund_id} · {as_of.isoformat()} · {facts.capability}\n"
            + "\n".join(
                f"{b.break_type.value}: accounting {b.accounting_value:,} vs custodian "
                f"{b.custodian_value:,}  (Δ {b.difference:,})"
                for b in case.breaks
            )
            # `facts.status` is the case's lifecycle state ("open"), not where it routed -- an
            # earlier version of this line printed it under the label "routed to", which read as
            # though the approval band were `open`. The band is derived by the control plane below.
            + f"\n\nmateriality {facts.impact}  ·  severity {facts.severity}"
            + "\n[dim]capability asserted, not classified — triage is S1.4[/dim]",
            title="Exception",
            border_style="yellow",
        )
    )

    gateway.clear_decision_log()
    with audit.case_trace(facts) as (_span, trace_id):
        band = gateway.route_for_approval(facts).metadata["band"]
        verdict, store = asyncio.run(
            investigator.investigate(case, agent, trace_id=trace_id)
        )

    # `Text`, not an f-string with markup. Model output and filing-derived text are interpolated
    # here, rich interprets square brackets as markup, and a stray closing tag raises MarkupError
    # mid-render -- which would end the recording. It matters most for the corporate-action path,
    # where a denial reason derives from attacker-authored notice text.
    summary = Text(verdict.root_cause, style="bold")
    summary.append(
        f"\n\nconfidence {verdict.confidence:.2f} · {len(store)} tool calls · "
        f"{len(verdict.citations)} citations",
        style="none",
    )
    console.print(
        Panel(
            summary,
            title=f"Verdict — {agent.ref} on {agent.model}",
            border_style="green" if verdict.asserts_a_cause else "red",
        )
    )

    evidence = Table(title="Evidence, as recorded — not as described", header_style="bold")
    for column in ("tool", "asked", "observed", "source"):
        evidence.add_column(column, overflow="fold")
    for citation in verdict.citations:
        observation = store.get(citation.observation_id)
        evidence.add_row(
            Text(observation.tool),
            # The arguments, on screen. Without them the panel cannot show *which* rate was read,
            # and a verdict citing a GBP lookup while asserting something about USD looked
            # identical to a correct one.
            Text(observation.args or "—"),
            Text(", ".join(f"{k}={v}" for k, v in sorted(observation.observed.items())) or "—"),
            Text(observation.source_uri or observation.source),
        )
    console.print(evidence)

    decisions = Table(title="Governance log", header_style="bold")
    for column in ("policy", "effect", "reason"):
        decisions.add_column(column, overflow="fold")
    for decision in gateway.decision_log():
        decisions.add_row(
            Text(decision.policy_id),
            Text("allow", style="green") if decision.allowed else Text("DENY", style="red"),
            Text(decision.reason[:110]),
        )
    console.print(decisions)

    console.print(
        f"  approval band [bold]{band}[/bold] · trace [dim]{trace_id}[/dim]\n"
        f"  No entry was posted. Drafting is the remediation agent's, and a human approves it."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
