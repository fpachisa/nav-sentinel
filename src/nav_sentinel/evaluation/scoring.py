"""What counts as right, stated precisely enough to be argued with.

Two metrics, chosen because a pass-through stub cannot fake either.

**Leg-level correction accuracy.** Not "did the total come out right" -- a stub returning the
negation of the control total satisfies that with no understanding at all, which is why the plan
demoted the closure sum to an invariant rather than a score. A leg is right when the account, the
currency and the amount all match to the cent. The failed trade has two legs and both must be
right; getting the net right by proposing one leg of the wrong size twice is not a correct entry.

**Root-cause accuracy.** Checked against the facts the golden says the cause turns on, not against
its prose. Two people describe a stale rate in different words; both name the same two rates and the
dates they belong to. So a cause is right when the verdict cites the facts the scenario demands and
states the figures the golden states.

Everything here is arithmetic over recorded outputs. Nothing asks a model whether a model was right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from nav_sentinel.evaluation.golden import TOLERANCE

if TYPE_CHECKING:  # pragma: no cover
    from nav_sentinel.evaluation.golden import Correction, Cycle, Scenario

#: Figures worth checking a cause against: at least three significant digits, so "2:1" and a year
#: are not mistaken for amounts.
_FIGURE = re.compile(r"-?\d[\d,]*\.\d+|-?\d{3,}")


@dataclass(frozen=True)
class LegScore:
    """One expected leg, and whether the proposal produced it."""

    expected: Correction
    matched: bool
    proposed: tuple[str, str | None, Decimal] | None = None

    @property
    def detail(self) -> str:
        if self.matched:
            return f"{self.expected.account} {self.expected.currency} {self.expected.amount}"
        if self.proposed is None:
            return f"missing {self.expected.account} {self.expected.currency} {self.expected.amount}"
        account, currency, amount = self.proposed
        return (
            f"expected {self.expected.account} {self.expected.currency} {self.expected.amount}, "
            f"got {account} {currency} {amount}"
        )


@dataclass
class ScenarioResult:
    """How one scenario went, for one system under test."""

    scenario: str
    capability_expected: str
    capability_actual: str | None = None
    #: Whether the break reached the agent the registry authorises for it.
    routed: bool = False
    legs: list[LegScore] = field(default_factory=list)
    #: Extra legs the proposal invented, which are as wrong as missing ones.
    spurious: list[tuple[str, str | None, Decimal]] = field(default_factory=list)
    cause_cites: frozenset[str] = frozenset()
    cause_states: frozenset[str] = frozenset()
    #: The capabilities any scenario covering this case declares. A composite case -- the USD cash
    #: balance carries both a dividend shortfall and a failed settlement -- is credited for naming
    #: any of them, because marking it wrong for choosing the larger cause would measure the
    #: fixture's shape rather than the fleet's judgement.
    classified_against: frozenset[str] = frozenset()
    cause_missing_facts: tuple[str, ...] = ()
    cause_missing_figures: tuple[str, ...] = ()
    refused: str | None = None
    #: A control rejected the proposal the model drafted. Kept distinct from `refused` because the
    #: two mean opposite things: `refused` is the fleet correctly declining (the adversarial pricing
    #: case's expected outcome), while this is a model mistake a control caught. The distinction is
    #: load-bearing for `posts_nothing_correctly` -- proposing nothing is the right answer for a
    #: reconciling item, and a rejected draft also produces no legs, so without this field a
    #: rejected draft would be *credited* as the correct answer.
    draft_rejected: str | None = None
    note: str = ""

    @property
    def classified(self) -> bool:
        if self.classified_against:
            return self.capability_actual in self.classified_against
        return self.capability_actual == self.capability_expected

    @property
    def legs_correct(self) -> bool:
        """Every expected leg produced, and nothing invented."""
        return bool(self.legs) and all(x.matched for x in self.legs) and not self.spurious

    @property
    def posts_nothing_correctly(self) -> bool:
        """For a reconciling item: proposing nothing *is* the right answer.

        A draft a control rejected is not that. It produced no legs for the opposite reason.
        """
        return not self.legs and not self.spurious and self.draft_rejected is None

    @property
    def cause_correct(self) -> bool:
        return not self.cause_missing_facts and not self.cause_missing_figures


def score_legs(
    expected: list[Correction],
    proposed: list[tuple[str, str | None, Decimal]],
    quantity_legs: list[tuple[str, str, Decimal]] | None = None,
) -> tuple[list[LegScore], list[tuple[str, str | None, Decimal]]]:
    """Match proposed legs to expected ones, and report what was left over.

    Money and share counts are matched against different fields, because the golden states them in
    different fields: a restatement carries `amount: 0.00` and `quantity: 96000.0000`. Matching a
    restatement on money alone scored a correct split as a miss; emitting the share count *as* an
    amount, which was the other way round, injected 96,000 into a sum of currency values.

    Greedy, one-to-one: a proposal cannot satisfy two expected legs with one line, and two identical
    proposed lines cannot both match the same expectation. Either would score a wrong entry as right.
    """
    remaining = list(proposed)
    shares = list(quantity_legs or [])
    scores: list[LegScore] = []
    for want in expected:
        if want.is_quantity:
            hit = next((c for c in shares if want.matches_quantity(c[0], c[2])), None)
            if hit is not None:
                shares.remove(hit)
                scores.append(
                    LegScore(expected=want, matched=True, proposed=(hit[0], None, hit[2]))
                )
            else:
                near = next((c for c in shares if c[0] == want.account), None)
                scores.append(
                    LegScore(
                        expected=want,
                        matched=False,
                        proposed=(near[0], None, near[2]) if near else None,
                    )
                )
            continue

        hit = next(
            (c for c in remaining if want.matches(c[0], c[1], c[2])),
            None,
        )
        if hit is not None:
            remaining.remove(hit)
            scores.append(LegScore(expected=want, matched=True, proposed=hit))
        else:
            # Report the nearest same-account line, if any, so a failure says how it was wrong.
            near = next((c for c in remaining if c[0] == want.account), None)
            scores.append(LegScore(expected=want, matched=False, proposed=near))
    return scores, remaining


def figures(text: str) -> frozenset[str]:
    """Numeric figures stated in a piece of prose, normalised so 1.15670000 matches 1.1567."""
    found: set[str] = set()
    for raw in _FIGURE.findall(text):
        try:
            found.add(str(Decimal(raw.replace(",", "")).normalize()))
        except InvalidOperation:  # pragma: no cover - the pattern only matches numbers
            continue
    return frozenset(found)


def score_cause(
    scenario: Scenario, root_cause: str, cited_facts: frozenset[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Which required facts the verdict failed to cite, and which stated figures it failed to state.

    Compared against the golden's own figures rather than its wording. The golden says *"the ECB
    reference rate for 2026-08-14 (1.15670000) instead of 2026-08-17 (1.15930000)"*; a verdict
    saying "applied the stale 2026-08-14 rate of 1.1567 where 1.1593 was published" is the same
    finding in different words, and a string comparison would score it wrong.
    """
    missing_facts = tuple(
        fact for fact in scenario.evidence_must_cite if fact not in cited_facts
    )
    stated = figures(root_cause)
    expected = figures(scenario.root_cause)
    missing_figures = tuple(sorted(expected - stated))
    return missing_facts, missing_figures


@dataclass(frozen=True)
class ClosureCheck:
    """Whether a cycle's corrections account for its control total."""

    control_total: Decimal
    corrections_in_base: Decimal
    residual: Decimal

    @property
    def closes(self) -> bool:
        return abs(self.residual) <= TOLERANCE

    def __str__(self) -> str:
        return (
            f"control total {self.control_total}, corrections {self.corrections_in_base} in base, "
            f"residual {self.residual.quantize(Decimal('0.0001'))} "
            f"({'closes' if self.closes else 'DOES NOT CLOSE'} to the cent)"
        )


def check_closure(cycle: Cycle, to_base) -> ClosureCheck:
    """`Σ corrections == −control_total`, in base currency.

    Converting first is not optional. Summing raw amounts across currencies gave a residual of
    −4,776.53 on a cycle whose corrections in fact close it: the ADR legs are USD and the control
    total is EUR. Once converted the residuals are 0.0059 and 0.0074 -- under a cent, which is why
    this is a tolerance and not an equality.

    Demoted from a score to an invariant deliberately: a stub returning the negation of the control
    total satisfies it with no understanding at all.
    """
    total = Decimal(0)
    for scenario in cycle.scenarios:
        for correction in scenario.expected_corrections:
            if correction.currency:
                total += to_base(correction.amount, correction.currency)
    return ClosureCheck(
        control_total=cycle.control_total,
        corrections_in_base=total,
        residual=cycle.control_total + total,
    )
