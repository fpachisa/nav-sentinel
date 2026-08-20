"""`python -m nav_sentinel.pipeline.approve_cli` -- the human step, as a real one.

This is the console a reviewer uses, and it is deliberately a separate entry point from anything
the fleet runs. It holds an `ApprovalAuthority`; the agent runtime never constructs one, which is
the whole reason `grant()` stopped being a module function -- anything that could import the module
could sign its own approval, and a control that accepts its own evidence from the party it is
controlling is not a control.

What this demonstrates, and the reason it exists rather than being described in a document:

* an approval is a **record**, keyed by case and band, signed by named principals with roles, and
  refused when the roles or the count are wrong for the band;
* the record is **append-only** -- a second grant for the same case and signers resolves to the
  same reference rather than creating a second one;
* and posting is **still denied afterwards**, because no published agent holds posting authority.
  An approval is necessary and not sufficient, which is the part a slide would skip.
"""

from __future__ import annotations

import sys
from datetime import date

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from nav_sentinel import composition
from nav_sentinel.control_plane import approvals, identity
from nav_sentinel.control_plane.approvals import ApprovalDenied, Principal
from nav_sentinel.control_plane.policies import PolicyViolation
from nav_sentinel.domain.models import ApprovalClass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    composition.configure()
    console = Console()

    if not argv:
        return _list_open(console)

    case_id = argv[0]
    signers = [_principal(spec) for spec in argv[1:]]
    if not signers:
        console.print(
            "[red]name at least one signer[/red]  "
            "e.g. approve_cli CASE-… alice:reviewer bob:senior_reviewer"
        )
        return 1
    return _approve(console, case_id, signers)


def _principal(spec: str) -> Principal:
    subject, _, role = spec.partition(":")
    return Principal(subject=subject, role=role or "reviewer")


def _list_open(console: Console) -> int:
    """Show what is waiting, from the repository rather than from memory."""
    store = composition.store()
    cases = store.cases_for("MERID-GEF", date(2026, 8, 17).isoformat())
    if not cases:
        console.print(
            "[yellow]no persisted cases. Run `make demo` first -- this console reads the "
            "repository, not a process that happens to still be running.[/yellow]"
        )
        return 1

    table = Table(title="Cases awaiting approval", header_style="bold")
    for column in ("case", "capability", "impact", "band", "investigator"):
        table.add_column(column, overflow="fold")
    for case in sorted(cases, key=lambda c: c["case_id"]):
        table.add_row(
            Text(case["case_id"]),
            Text(case.get("capability") or "—"),
            Text(str(case.get("impact") or "—")),
            Text(case.get("approval_band") or "—"),
            Text(case.get("authorised_agent") or "NONE"),
        )
    console.print(table)
    console.print(
        "  [dim]approve one with: python -m nav_sentinel.pipeline.approve_cli "
        "<case-id> alice:reviewer bob:senior_reviewer[/dim]"
    )
    return 0


def _approve(console: Console, case_id: str, signers: list[Principal]) -> int:
    store = composition.store()
    case = store.load_case(case_id)
    if case is None:
        console.print(f"[red]no persisted case {case_id}[/red]")
        return 1

    band = ApprovalClass(case["approval_band"])
    console.print(
        Panel(
            Text(
                f"{case_id}\n"
                f"{case.get('capability')} · impact {case.get('impact')} · band {band.value}\n"
                f"signers: {', '.join(str(p) for p in signers)}"
            ),
            title="Approval request",
            border_style="yellow",
        )
    )

    authority = composition.approval_authority()
    try:
        record = authority.grant(case_id, band, tuple(signers), note="approved via console")
    except ApprovalDenied as exc:
        # The interesting outcome, and the one worth showing: the band decides who may sign and how
        # many, so a single reviewer cannot clear a four-eyes case however senior they are.
        console.print(
            Panel(Text(str(exc)), title="Approval refused", border_style="red")
        )
        return 2

    console.print(
        Panel(
            Text(f"{record.ref}\ngranted for {band.value} by {', '.join(record.approvers)}"),
            title="Approval recorded",
            border_style="green",
        )
    )

    # And now the part that matters. An approval is necessary, not sufficient: posting is attempted
    # under the drafting agent's own identity and P-003 still denies it, because no published
    # manifest holds posting authority. A demo that stopped at "approved" would imply otherwise.
    from nav_sentinel.domain.models import ExceptionCase

    facts = ExceptionCase(
        case_id=case_id,
        fund_id=case["subject_id"],
        as_of=date.fromisoformat(case["as_of"]),
    ).to_facts()
    with identity.acting_as("remediation-agent"):
        try:
            from nav_sentinel.control_plane import gateway

            gateway.authorize_posting(facts, record.ref)
            console.print("[red]POSTED — no agent should be able to do this[/red]")
            return 1
        except PolicyViolation as exc:
            console.print(
                Panel(
                    Text(
                        f"{exc}\n\n"
                        f"The approval is recorded and valid. Posting is still refused, because no "
                        f"published agent holds posting authority -- an approval is necessary and "
                        f"not sufficient. A human posts, from their own session."
                    ),
                    title="Posting refused even with a valid approval",
                    border_style="cyan",
                )
            )
    console.print(f"  [dim]approval {record.ref} resolves: {approvals.resolve(record.ref) is not None}[/dim]")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
