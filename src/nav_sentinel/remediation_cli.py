"""`python -m nav_sentinel.remediation_cli` -- one NAV error remediation, end to end.

Twenty-eight days, four departments, seven deliveries. Outside both layers, beside `composition`
and `ta_cli`, because it is wiring: the remediation office declares the lifecycle and may not import
`casefile` or `repository`, transfer agency does not know the remediation office exists, and
something entitled to know about all of them has to run the sequence.

**The wall clock is compressed and nothing else is.** Each event is applied as a separate call that
reads the case from the store and writes it back, so between any two of them the process could be a
different process on a different revision -- the business dates are weeks apart and every figure is
computed from those. What this does not do is wait three weeks, and the narration says so.

Two real model calls happen here, at the two points where judgement is actually required:
transfer agency establishing who dealt, and the remediation office establishing whether this fund
has a pattern. Everything else -- the stage transitions, the threshold comparison, the approval
band -- is arithmetic and says so.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from nav_sentinel import composition
from nav_sentinel import remediation_runner as runner
from nav_sentinel.control_plane import casefile, gateway, identity
from nav_sentinel.memory import recurrence
from nav_sentinel.registry import discover
from nav_sentinel.remediation_office import materiality
from nav_sentinel.remediation_office.lifecycle import AWAITING
from nav_sentinel.remediation_office.models import NavErrorCase

TIMELINE = Path(__file__).resolve().parents[2] / "fixtures" / "data" / "remediation_timeline.json"

OFFICER = "remediation-officer"
IMPACT_CAPABILITY = "ta.dealing_impact"


def _seed_history(store: Any, timeline: dict, console: Console) -> None:
    """Record the fund's earlier errors this quarter, so the count comes from history."""
    for prior in timeline["prior_errors"]:
        case = NavErrorCase(
            case_id=prior["case_id"],
            fund_id=timeline["fund_id"],
            as_of=date.fromisoformat(prior["as_of"]),
            error_bps=Decimal(0),
            status="closed",
            note=prior["note"],
        )
        store.save_case(case.case_id, case.as_document())
    console.print(
        f"  [dim]seeded {len(timeline['prior_errors'])} earlier errors for "
        f"{timeline['fund_id']} this quarter — the recurrence count is read from these, "
        f"not from a literal[/dim]\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay one NAV error remediation.")
    parser.add_argument("--offline", action="store_true", help="skip the two model calls")
    parser.add_argument(
        "--case-id",
        default="",
        help="run under a different case id. Stage history is append-only, so a case that has "
             "already closed cannot be replayed -- which is the correct behaviour and not a "
             "limitation to work around.",
    )
    args = parser.parse_args()

    composition.configure()
    console = Console()
    timeline = json.loads(TIMELINE.read_text())
    store = composition.store()

    case = NavErrorCase(
        case_id=args.case_id or timeline["case_id"],
        fund_id=timeline["fund_id"],
        as_of=date.fromisoformat(timeline["nav_date"]),
        error_bps=Decimal(timeline["error_bps"]),
        capability="rem.materiality",
        note=timeline["events"][0]["note"],
    )

    console.print(
        Panel(
            Text(
                f"{case.fund_id} — NAV for {case.as_of.isoformat()} misstated by "
                f"{case.error_bps}bps\n"
                f"{len(timeline['events'])} events over "
                f"{timeline['events'][-1]['day']} days, four departments.\n\n"
                "The wall clock below is compressed. The business dates are not: every figure is "
                "computed from them, and the case is re-read from the store on every event."
            ),
            title="NAV error remediation",
            border_style="yellow",
        )
    )
    # A closed case cannot be reopened: stage history is append-only and `open_case` collides on
    # the existing sequence 0. Refusing with the case's actual stage is more use than a traceback,
    # and re-running with a new id is the honest way to replay -- deleting history to make a demo
    # repeatable would undermine the one claim this section is making.
    existing = casefile.load(store, case.case_id)
    if existing is not None:
        console.print(
            f"  [yellow]{case.case_id} already exists at stage "
            f"[bold]{existing.stage}[/bold] with {len(existing.history)} recorded "
            f"transitions.[/yellow]"
        )
        console.print(
            "  [dim]Stage history is append-only, so this case cannot be replayed. Re-run with "
            "--case-id to walk a new one.[/dim]"
        )
        _print_summary(console, store, case)
        return

    _seed_history(store, timeline, console)

    impact: dict[str, Any] | None = None
    assessment: materiality.Assessment | None = None

    for entry in timeline["events"]:
        event = entry["event"]
        payload: dict[str, Any] = {
            "case_id": case.case_id,
            "event": event,
            "note": entry["note"],
            "occurred_on": entry["occurred_on"],
        }

        # Transfer agency establishes who dealt. Reached by delegation: the remediation office asks
        # for a capability, the registry decides who answers, and that agent runs under its own
        # identity with its own allowlist.
        if event == "impact_reported":
            if args.offline:
                # The same tool the reporting agent would call, through the gateway and under that
                # agent's identity -- so P-001 and P-006 still apply and the number comes from the
                # register. Only the model is skipped. Reading a count from the fixture instead let
                # it disagree with the data: it said 41 investors while the register held four.
                impact = _ask_without_a_model(case, console)
            else:
                impact = _ask_transfer_agency(case, console)
            if impact is not None:
                case = case.model_copy(update={"affected_investors": impact["holders"]})
                payload["evidence"] = impact["citations"]

        # The remediation office establishes whether this fund has a pattern; the threshold
        # comparison is arithmetic.
        if event == "materiality_decided":
            try:
                assessment = _assess(case, store, timeline, console, offline=args.offline)
            except AssessmentDisputed as dispute:
                # The case stays where it is, awaiting a human. Advancing it on either number
                # would be choosing which of two contradictory records to believe.
                console.print(
                    Panel(
                        Text(str(dispute)),
                        title="Assessment disputed — escalated, case not advanced",
                        border_style="red",
                    )
                )
                break
            payload["note"] = assessment.rationale

        applied = runner.apply_event(store, payload)
        _print_event(console, entry, applied)

        # Persist the case document once impact is known, so the *next* error's recurrence count
        # can find it. A case that never files itself is invisible to the assessment after it.
        if event == "impact_reported":
            store.save_case(case.case_id, case.as_document())

        if (
            applied.stage == "materiality_determined"
            and assessment is not None
            and not assessment.requires_compensation
        ):
                console.print("  [dim]immaterial — the case closes with nothing paid[/dim]")
                runner.apply_event(
                    store,
                    {
                        "case_id": case.case_id,
                        "event": "closed_immaterial",
                        "note": assessment.rationale,
                    },
                )
                break

    _print_summary(console, store, case)


def _ask_transfer_agency(case: NavErrorCase, console: Console) -> dict[str, Any] | None:
    """Delegate to whichever agent the registry publishes for dealing impact."""
    brief = case.to_brief()
    with identity.acting_as(OFFICER):
        try:
            verdict, observations = gateway.delegate(IMPACT_CAPABILITY, brief)
        except gateway.UnroutableCapability as exc:
            console.print(f"  [yellow]{exc}[/yellow]")
            return None

    holders = _holder_count(observations)
    console.print(
        Panel(
            Text(
                f"{verdict.root_cause}\n\nconfidence {verdict.confidence:.2f} · "
                f"{len(observations)} observations · {len(verdict.citations)} citations"
            ),
            title="Transfer agency — reached by delegation, under its own identity",
            border_style="cyan",
        )
    )
    return {
        "holders": holders,
        "citations": [c.observation_id for c in verdict.citations],
    }


def _ask_without_a_model(case: NavErrorCase, console: Console) -> dict[str, Any]:
    """The reporting agent's own tool, called under its own identity. No model, same policy."""
    with identity.acting_as("dealing-impact-reporter"):
        # A real `date`, not its ISO form. `agent_surface._coerce` converts strings for a model
        # because ADK hands every argument over as text; a direct gateway call is the typed path
        # and gets the tool's actual signature.
        result = gateway.call_tool("register.dealt_on", case.fund_id, case.as_of)
    console.print(
        f"  [dim]offline: transfer agency's register queried directly under "
        f"dealing-impact-reporter's identity — {result['holders']} holders, "
        f"{result['units']} units on {result['trade_date']}. No model call.[/dim]"
    )
    return {"holders": int(result["holders"]), "citations": []}


def _holder_count(observations: Any) -> int:
    """The holder count from the observations, not from the model's prose.

    A number parsed out of a sentence is a number nobody checked. The projection recorded it as a
    fact, so that is what the assessment reads.
    """
    # `as_mapping().values()`, not the store itself: iterating an `ObservationStore` yields
    # observation *ids*, so a loop over it reads strings and every `.observed` lookup raises.
    for observation in observations.as_mapping().values():
        recorded = observation.observed.get("holders")
        if recorded:
            return int(Decimal(str(recorded)))
    return 0


class AssessmentDisputed(RuntimeError):
    """The officer's cited count and the record disagree, so nothing is assessed.

    The one outcome that must not be smoothed over. If the agent's evidence says three prior errors
    and the store says five, the threshold that applies is in question and a human has to look --
    proceeding on either number would be choosing which of two contradictory records to believe.
    """


def _cited_count(verdict: Any, observations: Any) -> int | None:
    """The prior-error count the officer actually **cited**, from the observations it named.

    Read from the cited observation rather than parsed out of the sentence. A number lifted from
    prose is a number nobody checked, and the projection recorded this one as a fact precisely so
    it could be compared against the record.
    """
    cited = {c.observation_id for c in verdict.citations}
    for observation_id, observation in observations.as_mapping().items():
        if observation_id in cited and "prior_errors" in observation.observed:
            return int(Decimal(str(observation.observed["prior_errors"])))
    return None


def _assess(
    case: NavErrorCase,
    store: Any,
    timeline: dict,
    console: Console,
    *,
    offline: bool,
) -> materiality.Assessment:
    """The officer establishes the history and the arithmetic decides -- and the two must agree.

    The count used below is read from the store, because a materiality threshold has to be
    reproducible. That would make the officer's answer decorative on its own, so its **cited** count
    is compared against the record and a disagreement stops the assessment. The model call is a
    control rather than a flourish: it produces the citation trail that shows *why* the count is
    what it is, and it is checked against the thing it claims to describe.
    """
    window = timeline["quarter_start"]
    counted = recurrence.prior_errors(
        store, case.fund_id, window, excluding=case.case_id
    )
    recorded = int(counted["prior_errors"])

    if not offline:
        with identity.acting_as(OFFICER):
            import asyncio

            from nav_sentinel.agents import investigator

            verdict, observations = asyncio.run(
                investigator.investigate(
                    case.to_brief(), discover.get(OFFICER)
                )
            )
        console.print(
            Panel(
                Text(
                    f"{verdict.root_cause}\n\nconfidence {verdict.confidence:.2f} · "
                    f"{len(observations)} observations · {len(verdict.citations)} citations"
                ),
                title="Remediation office — establishing the pattern",
                border_style="cyan",
            )
        )

        if not verdict.asserts_a_cause:
            raise AssessmentDisputed(
                f"the officer established no count: {verdict.unresolved[:200]}"
            )
        claimed = _cited_count(verdict, observations)
        if claimed is None:
            raise AssessmentDisputed(
                "the officer cited no observation carrying a prior-error count, so its answer "
                "cannot be checked against the record"
            )
        if claimed != recorded:
            raise AssessmentDisputed(
                f"the officer cited {claimed} prior errors and the record holds {recorded}. The "
                f"threshold that applies is in question, so nothing is assessed."
            )
        console.print(
            f"  [dim]cited count {claimed} agrees with the record ({recorded}) — the threshold "
            f"below rests on a number that was checked, not quoted[/dim]"
        )

    assessment = materiality.assess(
        error_bps=case.error_bps,
        affected_investors=case.affected_investors or 0,
        prior_errors=recorded,
        since=str(counted["since"]),
    )
    console.print(
        Panel(
            Text(assessment.rationale),
            title="Materiality — arithmetic, against a threshold the history selected",
            border_style="green" if assessment.material else "yellow",
        )
    )
    return assessment


def _print_event(console: Console, entry: dict, applied: runner.Applied) -> None:
    marker = "·" if applied.advanced else "="
    console.print(
        f"  day {entry['day']:>2}  {entry['occurred_on']}  {marker} "
        f"[bold]{applied.stage}[/bold]  "
        f"[dim]{entry['department']} — awaiting: {applied.awaiting}[/dim]"
    )


def _print_summary(console: Console, store: Any, case: NavErrorCase) -> None:
    history = store.stages_for(case.case_id)
    table = Table(title=f"Stage history — {case.case_id}", header_style="bold")
    for column in ("#", "From", "To", "Happened on", "Written at (wall clock)"):
        table.add_column(column)
    for entry in history:
        table.add_row(
            str(entry["sequence"]),
            str(entry.get("from") or "—"),
            str(entry["to"]),
            str(entry.get("occurred_on") or "—"),
            str(entry["recorded_at"])[:19],
        )
    console.print(table)

    facts = case.to_facts()
    band = gateway.route_for_approval(facts).metadata["band"]
    console.print(
        f"  approval band [bold]{band}[/bold] from "
        f"{facts.impact or 'no impact computed'} — derived by the control plane, not by this "
        f"process"
    )
    console.print(
        f"  final stage [bold]{history[-1]['to']}[/bold] · awaiting: "
        f"{AWAITING.get(history[-1]['to'], '')}"
    )
    dates = [str(e.get("occurred_on")) for e in history if e.get("occurred_on")]
    span = (
        (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days if dates else 0
    )
    console.print(
        f"\n  [dim]{len(history)} writes over {span} business days "
        f"({dates[0]} to {dates[-1]}). Both columns above are recorded: what happened when, and "
        f"when this system wrote it down. The sequence is real; the waiting is not.[/dim]"
    )


if __name__ == "__main__":
    main()
