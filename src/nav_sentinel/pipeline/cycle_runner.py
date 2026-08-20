"""Run one reconciliation cycle end to end.

Deliberately scoped to what exists. This is the deterministic spine: detect breaks against
tolerance, group them into cases, score materiality, derive the approval band in the control
plane, discover which specialist the registry authorises, and report whether the cycle closes.

No model is called. That is the design rather than a limitation for this stage: deciding whether
two numbers differ, and who must sign off on a difference of a given size, has to be reproducible
and testable. The investigators that explain *why* a break happened are S1, and asynchronous
dispatch is S3; both extend this loop rather than replacing it.

`make demo` runs this. It previously pointed at a module that did not exist.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from nav_sentinel import composition
from nav_sentinel.control_plane import audit, gateway, identity, telemetry
from nav_sentinel.domain import materiality, tolerance
from nav_sentinel.domain.cycle import group_into_cases
from nav_sentinel.domain.models import ExceptionCase
from nav_sentinel.registry import discover
from nav_sentinel.tools import books_and_records as bnr

FUND = "MERID-GEF"


class UnknownCycle(LookupError):
    """No NAV record exists for the requested date, so there is no cycle to run."""


def detect(as_of: date) -> list[ExceptionCase]:
    """Tolerance rules over both books. No model in the path."""
    breaks = (
        tolerance.detect_position_breaks(
            bnr.positions("accounting"), bnr.positions("custodian"), as_of
        )
        + tolerance.detect_cash_breaks(
            bnr.cash_movements("accounting"), bnr.cash_movements("custodian"), as_of
        )
    )
    return group_into_cases(breaks, FUND, as_of)


def run(as_of: date) -> dict:
    """One cycle. Returns a summary; prints nothing."""
    composition.configure()
    telemetry.configure_tracing(console=False)
    # One cycle is one unit of work, so it starts with an empty log. Without this a second run in
    # the same process reported the first run's decisions as well -- 56 for a cycle that recorded
    # 28 -- and every case's persisted trail carried the previous run's decisions too.
    gateway.clear_decision_log()

    custodian_nav = bnr.nav_record("custodian", FUND, as_of)
    accounting_nav = bnr.nav_record("accounting", FUND, as_of)
    if custodian_nav is None or accounting_nav is None:
        # A typed refusal, not an AttributeError on None. `as_of` arrives from a URL path and from
        # Pub/Sub message bodies, where any well-formed date is syntactically valid; only two have
        # NAV records. Callers need to tell "this cycle does not exist" from "this cycle failed",
        # because retrying the first forever is what Pub/Sub does with a non-2xx.
        raise UnknownCycle(
            f"no NAV record for {FUND} on {as_of.isoformat()}; "
            f"known cycles: {', '.join(r.as_of.isoformat() for r in bnr.nav_records('custodian'))}"
        )
    control_total = accounting_nav.net_assets - custodian_nav.net_assets

    to_base = _fixture_rates(as_of)
    rows = []
    for case in detect(as_of):
        materiality.score(case, custodian_nav, to_base)
        facts = case.to_facts()

        # Mark the log before the case's own decisions are recorded, so persisting one case does
        # not re-persist the previous case's.
        gateway.mark_decisions(case.case_id)
        with audit.case_trace(facts) as (_span, trace_id):
            # The band comes from the decision the gateway recorded, not from a second call. Two
            # call sites deriving the same governance decision is how the control plane came to
            # auto-clear a case the domain had floored.
            band = gateway.route_for_approval(facts).metadata["band"]
            agent = discover.discover_for_capability(facts.capability)
            if agent is not None:
                # The tool call is policed even though nothing is investigated yet: the point is
                # that the path an investigator will take is already governed.
                with identity.acting_as(agent.agent_id):
                    gateway.call_tool("registry.coverage")

            # Persisted inside the trace, so the stored decisions carry the trace id that the span
            # they were emitted under also carries. A cycle whose record only exists in the memory
            # of the instance that ran it is not an audit trail; Cloud Run scales to zero.
            _persist(case, facts, band, agent, trace_id)
        rows.append(
            {
                "case_id": case.case_id,
                "capability": facts.capability,
                "impact": facts.impact,
                "band": band,
                "authorised_agent": agent.ref if agent else None,
                "trace_id": trace_id,
            }
        )

    return {
        "as_of": as_of,
        "control_total": control_total,
        "cases": rows,
        "decisions": len(gateway.decision_log()),
    }


def _persist(case, facts, band: str, agent, trace_id: str | None) -> None:
    """Write the case and its governance decisions to the repository.

    Decisions are written with an explicit sequence, because the store keys them by it and refuses
    a duplicate: that is what makes the log append-only rather than merely appended-to. The sequence
    is the position within *this case*, so two instances working different cases never collide and
    two working the same one do -- which is a real conflict and should surface as one.
    """
    from nav_sentinel import composition

    store = composition.store()
    store.save_case(
        case.case_id,
        {
            "case_id": case.case_id,
            "subject_id": facts.subject_id,
            "as_of": facts.as_of.isoformat(),
            "capability": facts.capability,
            "status": facts.status,
            "severity": facts.severity,
            "impact": str(facts.impact) if facts.impact else None,
            "approval_band": band,
            "authorised_agent": agent.ref if agent else None,
            "trace_id": trace_id,
            "break_ids": [b.break_id for b in case.breaks],
        },
    )
    for sequence, decision in enumerate(gateway.decisions_since(case.case_id)):
        store.record_decision(case.case_id, trace_id, sequence, decision)


def _fixture_rates(as_of: date):
    """Convert using the rates already in the books, rather than re-fetching them."""
    rates = {"EUR": Decimal(1)}
    for p in bnr.positions("custodian"):
        if p.as_of == as_of:
            rates.setdefault(p.local_currency, p.fx_rate)

    def convert(amount: Decimal, currency: str) -> Decimal:
        return amount / rates.get(currency, Decimal(1))

    return convert


def main() -> int:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    as_of = date.fromisoformat(
        (bnr.nav_records("custodian")[-1]).as_of.isoformat()
    )
    result = run(as_of)

    table = Table(title=f"{FUND} — reconciliation cycle {as_of}", header_style="bold")
    for column in ("Case", "Capability", "Impact", "Band", "Authorised investigator"):
        table.add_column(column)
    for row in result["cases"]:
        table.add_row(
            row["case_id"][:14],
            row["capability"],
            str(row["impact"]) if row["impact"] else "not computed",
            row["band"],
            row["authorised_agent"] or "[red]NONE[/red]",
        )
    console.print(table)
    console.print(
        f"  control total [bold]{result['control_total']:,}[/bold] EUR · "
        f"{len(result['cases'])} cases · {result['decisions']} policy decisions recorded"
    )
    console.print(
        "\n  [dim]No model was called. Investigators land in S1, asynchronous dispatch in S3; "
        "both extend this loop.[/dim]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
