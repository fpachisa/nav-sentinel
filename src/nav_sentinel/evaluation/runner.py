"""`make eval` -- run both systems over the golden and report them side by side.

Two things this is careful about.

**It records before it scores.** A live run costs minutes and a model's answer varies, so the run
writes a JSON report and the scoring reads it. That means a number in the README can be traced to a
recorded run rather than to a paragraph, `--score` can re-score without spending a model call, and a
disagreement about a figure is settled by opening the file.

**N is stated everywhere it is quoted.** Six scenarios across two cycles. One miss is 16.7%, and a
percentage over six cases invites a confidence it does not carry -- so every rate is printed with its
fraction, and the report says `indicative at N=6` in the same breath.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from nav_sentinel import composition
from nav_sentinel.agents import contract, investigator, remediation, triage
from nav_sentinel.control_plane import identity
from nav_sentinel.domain import materiality, signals
from nav_sentinel.domain.models import ExceptionCase
from nav_sentinel.evaluation import baseline, golden, scoring
from nav_sentinel.pipeline import cycle_runner
from nav_sentinel.registry import discover
from nav_sentinel.tools import books_and_records as bnr

if TYPE_CHECKING:  # pragma: no cover
    from nav_sentinel.evaluation.golden import Scenario

REPORT = Path(__file__).resolve().parents[3] / "eval" / "last_run.json"


@dataclass
class SystemScore:
    """One system's totals. Fractions, not just rates -- see the module docstring on N."""

    name: str
    scenarios: list[scoring.ScenarioResult] = field(default_factory=list)
    #: Per cycle: the expected legs and how each fared, plus anything invented. Kept so a number in
    #: the README can be traced to the legs behind it.
    cycle_legs: list[tuple[str, list[str], list[str]]] = field(default_factory=list)

    def _rate(self, predicate) -> tuple[int, int]:
        applicable = [s for s in self.scenarios if s.note != "skipped"]
        return sum(1 for s in applicable if predicate(s)), len(applicable)

    @property
    def classification(self) -> tuple[int, int]:
        return self._rate(lambda s: s.classified)

    @property
    def causes(self) -> tuple[int, int]:
        return self._rate(lambda s: s.cause_correct)


def scenarios_by_case(
    scenarios: list[Scenario], cases: list[ExceptionCase]
) -> dict[str, list[Scenario]]:
    """Which scenarios each detected case covers. Many-to-many, because it genuinely is.

    The golden keys a scenario by ISIN; the detector groups breaks by security *or by currency*. Those
    do not line up, and pretending they do was scoring the fleet against the wrong thing:

    * An under-withheld ADR dividend names a security but manifests as a **cash balance** break with
      no ISIN at all -- so ISIN matching skipped it and quietly scored the eval out of five.
    * The USD cash case is a **composite**: the dividend shortfall and a failed settlement both land
      in one balance, and the settlement is 99% of it. Triage answering `nav.settlement` there is
      defensible, and marking it wrong because one scenario says `nav.corporate_action` measures the
      fixture's shape rather than the fleet's judgement.
    * The failed trade spans two cases -- a position break and the cash leg.

    So a case is credited when it names a capability of *any* scenario it covers, and leg accuracy is
    scored across the whole cycle rather than per case. Both are stated in the report, because a
    metric whose definition is not stated is not a measurement.
    """
    covered: dict[str, list[Scenario]] = {case.case_id: [] for case in cases}
    for scenario in scenarios:
        for case in cases:
            if scenario.isin and any(b.isin == scenario.isin for b in case.breaks):
                covered[case.case_id].append(scenario)
        wanted = {
            c.currency for c in scenario.expected_corrections if c.leg == "cash" and c.currency
        }
        for case in cases:
            currencies = {b.currency for b in case.breaks if b.currency and not b.isin}
            if wanted and currencies & wanted and scenario not in covered[case.case_id]:
                covered[case.case_id].append(scenario)
    return covered


async def _run_fleet(
    case: ExceptionCase, scenarios: list[Scenario]
) -> tuple[scoring.ScenarioResult, list[tuple[str, str | None, Decimal]]]:
    """Triage, investigate, draft -- the whole path. Returns the score and the legs it produced."""
    expected = {s.capability for s in scenarios}
    result = scoring.ScenarioResult(
        scenario=", ".join(s.scenario for s in scenarios) or case.case_id,
        capability_expected=" | ".join(sorted(expected)) or "nav.unclassified",
    )

    classification = await triage.classify(case, discover.get("triage-agent"))
    result.capability_actual = classification.capability
    result.classified_against = frozenset(expected)
    case.category = contract.category_for(classification.capability)

    agent = discover.discover_for_capability(case.capability)
    if agent is None:
        # Correctly classified and refused for want of an authorised investigator is a *result*, and
        # it is the adversarial pricing case's expected outcome.
        result.refused = f"no authorised investigator for {case.capability}"
        return result, ([], [])
    result.routed = True

    verdict, store = await investigator.investigate(case, agent)
    cited = store.facts_from([c.observation_id for c in verdict.citations])
    result.cause_cites = cited
    result.cause_missing_facts, result.cause_missing_figures = _score_cause_union(
        scenarios, verdict.root_cause, cited
    )
    if not verdict.asserts_a_cause:
        result.refused = verdict.unresolved[:200]
        return result, ([], [])

    proposal = await remediation.draft(case, verdict, discover.get("remediation-agent"))
    return result, (proposal.nav_legs, proposal.quantity_legs)


def _score_cause_union(
    scenarios: list[Scenario], root_cause: str, cited: frozenset[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """A composite case is credited against whichever scenario it explains best.

    Requiring the union would demand one sentence state every figure of two unrelated causes; the
    fleet is asked to explain the break, not to enumerate the fixture.
    """
    if not scenarios:
        return (), ()
    scored = [scoring.score_cause(s, root_cause, cited) for s in scenarios]
    return min(scored, key=lambda pair: len(pair[0]) + len(pair[1]))


def _run_baseline(
    case: ExceptionCase, scenarios: list[Scenario]
) -> tuple[scoring.ScenarioResult, list[tuple[str, str | None, Decimal]]]:
    with identity.acting_as("triage-agent"):
        produced = baseline.run(case, signals.for_case(case))
    expected = {s.capability for s in scenarios}
    result = scoring.ScenarioResult(
        scenario=", ".join(s.scenario for s in scenarios) or case.case_id,
        capability_expected=" | ".join(sorted(expected)) or "nav.unclassified",
        capability_actual=produced.capability,
        classified_against=frozenset(expected),
        routed=discover.discover_for_capability(produced.capability) is not None,
    )
    result.cause_cites = produced.cited_facts
    result.cause_missing_facts, result.cause_missing_figures = _score_cause_union(
        scenarios, produced.root_cause, produced.cited_facts
    )
    return result, (produced.legs, [])


def run(live: bool = True) -> dict[str, Any]:
    """Score both systems over every case, and legs across each cycle."""
    composition.configure()
    reference = golden.load()
    fleet, heuristic = SystemScore("fleet"), SystemScore("heuristic baseline")
    closures: list[str] = []
    leg_totals = {"fleet": [0, 0], "heuristic baseline": [0, 0]}
    uncovered: list[str] = []

    for cycle in reference.cycles:
        as_of = cycle.nav_date
        to_base = cycle_runner._fixture_rates(as_of)
        closures.append(f"{as_of.isoformat()}: {scoring.check_closure(cycle, to_base)}")
        cases = cycle_runner.detect(as_of)
        custodian = bnr.nav_record("custodian", reference.fund_id, as_of)
        for case in cases:
            materiality.score(case, custodian, to_base)

        covered = scenarios_by_case(cycle.scenarios, cases)
        matched = {s.scenario for group in covered.values() for s in group}
        uncovered.extend(
            s.scenario for s in cycle.scenarios if s.scenario not in matched
        )

        expected_legs = [
            correction
            for scenario in cycle.scenarios
            for correction in scenario.expected_corrections
        ]
        produced: dict[str, list] = {"fleet": [], "heuristic baseline": []}
        shares: dict[str, list] = {"fleet": [], "heuristic baseline": []}

        for case in cases:
            scenarios = covered[case.case_id]
            result, (legs, quantity) = _run_baseline(case, scenarios)
            heuristic.scenarios.append(result)
            produced["heuristic baseline"].extend(legs)
            shares["heuristic baseline"].extend(quantity)
            if live:
                result, (legs, quantity) = asyncio.run(_run_fleet(case, scenarios))
                fleet.scenarios.append(result)
                produced["fleet"].extend(legs)
                shares["fleet"].extend(quantity)

        # Legs are scored across the whole cycle. A scenario's corrections can span two cases -- the
        # failed trade has a securities leg and a cash leg in different balances -- so per-case
        # matching would mark both halves of a correct answer as missing.
        for name in leg_totals if live else ["heuristic baseline"]:
            scores, spurious = scoring.score_legs(expected_legs, produced[name], shares[name])
            leg_totals[name][0] += sum(1 for x in scores if x.matched)
            leg_totals[name][1] += len(scores)
            (fleet if name == "fleet" else heuristic).cycle_legs.append(
                (as_of.isoformat(), [x.detail for x in scores], [f"{a} {c} {v}" for a, c, v in spurious])
            )

    total = sum(len(c.scenarios) for c in reference.cycles)
    report = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "sample_size": total,
        "scored": total - len(uncovered),
        "skipped": uncovered,
        "closure": closures,
        "metric_definitions": {
            "classification": (
                "per detected case, credited when the named capability belongs to any golden "
                "scenario that case covers -- the USD cash balance carries two"
            ),
            "legs": (
                "per cycle, matched one-to-one against every expected correction; a scenario's "
                "legs can span two cases, so per-case matching would fail a correct answer"
            ),
            "causes": (
                "per case, against the best-matching scenario it covers: required facts cited and "
                "the golden's own figures stated, compared numerically rather than as prose"
            ),
        },
        "systems": {
            score.name: {
                "classification": list(score.classification),
                "legs": leg_totals[score.name],
                "causes": list(score.causes),
                "cycle_legs": score.cycle_legs,
                "cases": [
                    {
                        "covers": s.scenario,
                        "expected": s.capability_expected,
                        "actual": s.capability_actual,
                        "routed": s.routed,
                        "classified": s.classified,
                        "cause_correct": s.cause_correct,
                        "missing_facts": list(s.cause_missing_facts),
                        "missing_figures": list(s.cause_missing_figures),
                        "refused": s.refused,
                    }
                    for s in score.scenarios
                ],
            }
            for score in ((fleet, heuristic) if live else (heuristic,))
        },
    }
    REPORT.write_text(json.dumps(report, indent=2, default=str) + "\n")
    return report


def render(report: dict[str, Any], console: Console | None = None) -> None:
    console = console or Console()
    n = report["sample_size"]

    scored = report["scored"]
    table = Table(
        title=f"Accuracy against the golden — indicative at N={scored}", header_style="bold"
    )
    table.add_column("metric", overflow="fold")
    for name in report["systems"]:
        table.add_column(name, justify="right")

    for label, key in (
        ("classification", "classification"),
        ("leg-level correction", "legs"),
        ("root cause", "causes"),
    ):
        row = [Text(f"{label}\n[{report['metric_definitions'][key]}]")]
        for name in report["systems"]:
            hit, total = report["systems"][name][key]
            row.append(Text(f"{hit}/{total}" + (f"  ({hit / total:.0%})" if total else "")))
        table.add_row(*row)
    console.print(table)

    for line in report["closure"]:
        console.print(f"  closure invariant · {line}")
    if report["skipped"]:
        # Loudly, and not in dim text. A scenario the harness could not match is missing evidence,
        # and a denominator that quietly shrank would have reported the remainder as the whole.
        console.print(
            f"\n  [red]{len(report['skipped'])} of {n} scenarios were not scored: "
            f"{', '.join(report['skipped'])}. The rates above are out of {scored}, not {n}.[/red]"
        )

    console.print(
        f"\n  [dim]N={scored}. One miss is {1 / scored:.1%}, so these are indicative and not a "
        f"benchmark.\n"
        f"  The baseline is a deterministic rule engine over the same signals, not a strawman:\n"
        f"  where it matches, the finding is that the work is arithmetic. Recorded in "
        f"{REPORT.relative_to(REPORT.parents[1])}.[/dim]"
    )


def main() -> int:
    live = "--score" not in sys.argv[1:]
    report = run(live=live) if live or not REPORT.exists() else json.loads(REPORT.read_text())
    render(report)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
