"""`python -m nav_sentinel.ta_cli` -- run one transfer-agency cycle against the share register.

Outside both layers, beside `composition` and `fleet_cli`, for exactly the reason they are: this is
the wiring. The transfer-agency package may not import `nav_sentinel.agents`, and the investigator
does not know transfer agency exists. Something has to introduce them, and the composition root is
the one place entitled to know about both.

What it demonstrates is the claim the whole architecture rests on: **the agent running here is the
same `investigate()` the fund fleet uses.** Not a copy, not a subclass, not a second implementation
that drifts -- the same function, given a manifest and a prompt this process ships, reading tools
this process registered, policed by the same gateway. A second process gets the agentic machinery,
not just the plumbing.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from nav_sentinel import composition
from nav_sentinel.agents import investigator
from nav_sentinel.control_plane import gateway
from nav_sentinel.registry import discover
from nav_sentinel.transfer_agency import cycle

FUND = "MERID-GEF"


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile a share register.")
    parser.add_argument("--fund", default=FUND)
    parser.add_argument("--as-of", default="2026-08-17", type=date.fromisoformat)
    args = parser.parse_args()

    composition.configure()
    console = Console()

    async def investigate(brief):
        # Routing is the registry's decision, not this file's. Asking it per brief is what makes
        # the NONE in `make registry` mean something: an unrouted capability stops here.
        agent = discover.discover_for_capability(brief.capability)
        if agent is None:
            raise SystemExit(f"no published agent handles {brief.capability}")
        verdict, store = await investigator.investigate(brief, agent)
        console.print(
            Panel(
                Text(
                    f"{verdict.root_cause}\n\nconfidence {verdict.confidence:.2f} · "
                    f"{len(store)} tool calls · {len(verdict.citations)} citations"
                ),
                title=f"{agent.ref} on {agent.model}",
                border_style="cyan",
            )
        )
        return verdict

    results = asyncio.run(cycle.run(args.fund, args.as_of, investigate=investigate))
    if not results:
        console.print("[green]register agrees with the unit ledger[/green]")
        return

    table = Table(title=f"Share register — {args.fund} at {args.as_of}", header_style="bold")
    for column in ("Holder", "Capability", "Units", "Band", "Correction"):
        table.add_column(column)

    for result in results:
        case = result.case
        # The band comes from the control plane, derived from a units magnitude. Nothing here
        # computes it, and `to_facts` is deliberately lossy on the way over.
        band = gateway.route_for_approval(case.to_facts()).metadata["band"]
        table.add_row(
            case.case_id.split("-holder-")[-1],
            case.capability,
            f"{case.units_at_risk:,}",
            band,
            (
                Text(
                    f"+{result.restatement.units:,} units on "
                    f"{result.restatement.settlement_date.isoformat()}",
                    style="green",
                )
                if result.resolved
                else Text(result.refused or "refused", style="yellow")
            ),
        )
    console.print(table)

    for result in results:
        if result.resolved:
            console.print(
                Panel(
                    Text(result.restatement.rationale),
                    title="Correction — arithmetic, no model",
                    border_style="green",
                )
            )


if __name__ == "__main__":
    main()
