"""Drafting a correction, which is as far as any agent is permitted to go.

The fleet's whole claim rests here. An investigator explains a break; this agent proposes what to
do about it; a human approves; only then does anything reach the ledger. Every one of those steps
is a separate authority, and this module holds exactly one of them.

Three things it must get right.

**It drafts, and cannot post.** P-002 grants drafting only to this agent; P-003 denies posting to
every published agent unconditionally, including this one. `authorize_drafting` is called before a
proposal is built, so the authority is checked rather than assumed -- and the proposal it returns
has no method that commits anything.

**Not every break is a journal.** Four of the six seeded scenarios are; a split is a quantity
restatement with no amount, and a trade-date-versus-settlement-date difference is a reconciling
item that posts nothing at all. Forcing those into a journal shape would fabricate an entry for a
break that needs none, which is worse than proposing nothing.

**The arithmetic is checked, not trusted.** A journal must balance in every currency it touches,
and the expected residual is computed here from the case's own impact rather than taken from the
model. A model that proposes a plausible but unbalanced entry is refused by the type, and one that
misstates the residual cannot, because it never supplies it.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from nav_sentinel.agents.contract import UNKNOWN
from nav_sentinel.agents.investigator import (
    UnparseableAnswer,
    adk_name,
)
from nav_sentinel.control_plane import gateway, identity, telemetry
from nav_sentinel.control_plane.policies import PolicyViolation
from nav_sentinel.domain.models import (
    NAV_ACCOUNTS,
    JournalEntryLine,
    Outcome,
    QuantityRestatementLine,
    RemediationProposal,
)

if TYPE_CHECKING:  # pragma: no cover
    from nav_sentinel.agents.contract import Verdict
    from nav_sentinel.domain.models import ExceptionCase
    from nav_sentinel.registry.models import AgentManifest

logger = logging.getLogger(__name__)

APP_NAME = "nav-sentinel"

#: The accounts a proposal may name. A closed set, because an account a model invented is one no
#: ledger has, and a reviewer cannot check a posting to `fx_adjustment_suspense_2`.
ACCOUNTS = (
    "investments_at_market",
    "cash_at_bank",
    "dividends_receivable",
    "accrued_expenses",
    "unrealised_fx",
    "realised_gain_loss",
    "withholding_tax_expense",
    "stock_record",
)


class DraftLine(BaseModel):
    """One leg, as the model states it. Debit or credit, never both."""

    model_config = ConfigDict(extra="ignore")

    account: Literal[ACCOUNTS]  # type: ignore[valid-type]
    currency: str = Field(default="", description="ISO code, e.g. USD. Empty for a quantity line.")
    debit: Decimal = Decimal(0)
    credit: Decimal = Decimal(0)
    narrative: str = ""


class DraftQuantityLine(BaseModel):
    """A share count corrected. Both counts, so a reviewer can check them against the books."""

    model_config = ConfigDict(extra="ignore")

    account: Literal[ACCOUNTS] = "stock_record"  # type: ignore[valid-type]
    isin: str
    from_quantity: Decimal
    to_quantity: Decimal
    narrative: str = ""


class ProposalDraft(BaseModel):
    """What the model returns. Permissive, like the verdict draft, and for the same reason: ADK
    validates `output_schema` inside the runner, so a cross-field rule raises there rather than
    coming back as a bad answer."""

    model_config = ConfigDict(extra="ignore")

    outcome: Literal["journal_entry", "reconciling_item", "quantity_restatement"] = Field(
        description=(
            "journal_entry when money must move; quantity_restatement when only a share count is "
            "wrong and market value agrees; reconciling_item when both books are right and the "
            "difference is timing, in which case propose no lines at all."
        )
    )
    lines: list[DraftLine] = Field(default_factory=list)
    quantity_lines: list[DraftQuantityLine] = Field(default_factory=list)
    rationale: str = Field(default="", description="Why this entry corrects the cause.")


class NotDraftable(RuntimeError):
    """The verdict does not support a proposal, so none is drafted."""


def _base_currency(fund_id: str) -> str:
    """The fund's base currency, read through the gateway.

    Stated in the prompt because the model got it wrong without it: the FX correction came back as
    `investments_at_market USD -86,625.48` -- the right account and the right amount to the cent, in
    the security's local trading currency rather than the currency the position is carried at in net
    assets. An entry in the wrong currency is not a smaller error than an entry of the wrong amount.
    """
    for fund in gateway.call_tool("books_and_records.funds"):
        if fund.fund_id == fund_id:
            return fund.base_currency
    return "EUR"


def _instruction(manifest: AgentManifest, case: ExceptionCase, verdict: Verdict) -> str:
    """The prompt. States the accounting conventions explicitly, because a model asked to correct a
    fund's books without them will produce something plausible and wrong."""
    breaks = "\n".join(
        f"  - {b.break_type.value}: accounting {b.accounting_value}, custodian "
        f"{b.custodian_value}, difference {b.difference}"
        + (f", ISIN {b.isin}" if b.isin else "")
        + (f", currency {b.currency}" if b.currency else "")
        for b in case.breaks
    )
    base = _base_currency(case.fund_id)
    return f"""You are the {manifest.display_name} for a fund administrator.

An investigator has established why the books disagree. Draft the correction. You do not post it:
a human approves every entry, and nothing you produce reaches the ledger on your say-so.

The case:
  fund {case.fund_id}, base currency {base}, valuation date {case.as_of.isoformat()}
  case {case.case_id}
{breaks}

The established cause:
  {verdict.root_cause}

Which side is wrong matters: the correction adjusts the **accounting** book to agree with the
custodian, unless the cause says the custodian is the one in error.

Choose the outcome first:
  - journal_entry -- money must move. Every entry must balance **within each currency**: the debits
    and credits in USD must net to zero, and so must those in EUR. Correcting an overstated
    position means crediting investments_at_market and debiting the contra
    (unrealised_fx for a valuation error, withholding_tax_expense for unreclaimable withholding,
    realised_gain_loss for a disposal).
  - quantity_restatement -- only the share count is wrong and market value agrees exactly, as with
    an unapplied split. State from_quantity and to_quantity. No amounts.
  - reconciling_item -- both books are right and the difference is timing, such as a trade
    recognised on trade date by one side and settlement date by the other. Propose **no lines**.
    There is nothing to correct, and inventing an entry would create an error rather than fix one.

Currency matters as much as the amount. A **market value** correction is stated in the fund's base
currency, {base}, because that is the currency the position is carried at in net assets -- not the
security's local trading currency. A **cash** correction is stated in the currency of the cash
account it touches.

Available accounts: {", ".join(ACCOUNTS)}. Use no others.

State amounts to the cent, as decimals. Do not restate the residual or the approval level: both are
computed from the case, not taken from you.
"""


async def draft(
    case: ExceptionCase,
    verdict: Verdict,
    manifest: AgentManifest,
    *,
    trace_id: str | None = None,
) -> RemediationProposal:
    """Draft a correction for a case whose cause is established.

    Raises `NotDraftable` when the verdict asserts no cause: there is nothing to correct, and a
    proposal built on an UNKNOWN root cause would be a guess wearing a verdict's clothes.
    """
    from google.adk.agents import Agent

    from nav_sentinel.config import configure_sdk_environment

    if not verdict.asserts_a_cause:
        raise NotDraftable(
            f"the verdict on {case.case_id} asserts no cause "
            f"({verdict.unresolved or UNKNOWN}), so there is nothing to correct"
        )
    configure_sdk_environment()

    with identity.acting_as(manifest.agent_id):
        # The *only* drafting check, and it is the gateway's. There was an agent-side copy above
        # this line -- `if manifest.agent_id != "remediation-agent" ...` -- which fired first, so
        # P-002 was never reached: mutating the policy to always ALLOW broke nothing in this
        # section's suite, and an investigator's attempted draft left no decision in the governance
        # log at all. An agent checking its own permissions is the anti-pattern the gateway module
        # docstring opens with, and a hardcoded agent id in the agents layer is how it crept back.
        gateway.authorize_drafting()

        agent = Agent(
            name=adk_name(manifest.agent_id),
            model=manifest.model,
            instruction=_instruction(manifest, case, verdict),
            tools=[],
            output_schema=ProposalDraft,
            output_key="proposal",
        )
        with telemetry.span(
            "nav_sentinel.remediation",
            **{
                "nav.case.id": case.case_id,
                "nav.agent.ref": manifest.ref,
                "nav.agent.model": manifest.model,
                # So the drafting step joins the trace of the case it is drafting for.
                "nav.case.trace_id": trace_id or "",
            },
        ) as span:
            drafted = await _run(agent, case)
            proposal = _finalise(drafted, case, manifest)
            span.set_attribute("nav.proposal.outcome", proposal.outcome.value)
            span.set_attribute("nav.proposal.legs", len(proposal.lines))
            span.set_attribute("nav.proposal.balances", proposal.balances)
            span.set_attribute("nav.proposal.requires", proposal.requires.value)
            return proposal


async def _run(agent, case: ExceptionCase) -> ProposalDraft:
    from google.adk.agents.run_config import RunConfig
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    runner = InMemoryRunner(agent, app_name=APP_NAME)
    session = await runner.session_service.create_session(app_name=APP_NAME, user_id="fleet")
    reply = ""
    async for event in runner.run_async(
        user_id="fleet",
        session_id=session.id,
        new_message=types.Content(
            role="user", parts=[types.Part(text=f"Draft the correction for {case.case_id}.")]
        ),
        run_config=RunConfig(max_llm_calls=5),
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    reply = part.text

    validated = (
        await runner.session_service.get_session(
            app_name=APP_NAME, user_id="fleet", session_id=session.id
        )
    ).state.get("proposal")
    if validated:
        return ProposalDraft.model_validate(validated)
    if not reply.strip():
        raise UnparseableAnswer("the drafting agent returned nothing")
    return ProposalDraft.model_validate_json(reply)


def _finalise(
    drafted: ProposalDraft, case: ExceptionCase, manifest: AgentManifest
) -> RemediationProposal:
    """Build the typed proposal, computing what the model must not supply.

    The residual and the approval band come from the case, not from the draft. A model asked for
    either would have an interest in understating both -- a smaller residual reads as a better
    correction, and a lower band needs fewer signatures.
    """
    agent, _, version = manifest.ref.partition("@")
    base = _base_currency(case.fund_id)
    return RemediationProposal(
        proposal_id=_proposal_id(case, drafted),
        outcome=Outcome(drafted.outcome),
        lines=[
            JournalEntryLine(
                account=line.account,
                # The fund's base currency when the model omits one -- never a break's `currency`,
                # which on a market-value break is the security's *local trading* currency while the
                # difference is already in base. That fallback silently produced
                # `investments_at_market USD -86,625.48` for the FX case: verbatim the defect this
                # section's prompt fix was written for, still hardcoded in the code path. It also
                # read `breaks[0]`, so a two-break case got whichever currency happened to be first,
                # and a case with no breaks raised IndexError.
                currency=line.currency or base,
                debit=line.debit,
                credit=line.credit,
                narrative=line.narrative,
            )
            for line in drafted.lines
        ],
        quantity_lines=[
            QuantityRestatementLine(
                account=line.account,
                isin=line.isin,
                from_quantity=line.from_quantity,
                to_quantity=line.to_quantity,
                narrative=line.narrative,
            )
            for line in drafted.quantity_lines
        ],
        # Genuinely computed now. It was `Decimal(0)` under this exact comment, so every proposal
        # reported "closes exactly" whether it did or not -- a control reporting success in a state
        # where it never ran, and against PLAN.md's own words, which name `expected_residual` as
        # *the* reason S4 is mandatory: "already exists as the hook and nothing computes it".
        expected_residual=_residual(case, drafted),
        rationale=drafted.rationale,
        proposed_by_agent=agent,
        proposed_by_version=version or manifest.version,
        # The band the control plane derived, not one the model chose. A proposal that set its own
        # approval level would decide how many humans need to look at it.
        requires=case.approval_class or _band_for(case),
    )


def _residual(case: ExceptionCase, drafted: ProposalDraft) -> Decimal:
    """What would still be unreconciled after posting this draft.

    The case's own signed impact plus the draft's effect on net assets, in base currency. Zero means
    the entry closes the break; anything else is the amount a reviewer still has to explain, and it
    is the single most useful number on a proposal.

    Computed here, never taken from the model: a model asked for its own residual has an interest in
    reporting zero.
    """
    from nav_sentinel.domain import cycle
    from nav_sentinel.pipeline.cycle_runner import _fixture_rates

    to_base = _fixture_rates(case.as_of)
    effect = Decimal(0)
    for line in drafted.lines:
        if line.account not in NAV_ACCOUNTS:
            continue
        currency = line.currency or _base_currency(case.fund_id)
        effect += to_base(line.debit - line.credit, currency)
    return (cycle.signed_impact_base(case, to_base) + effect).quantize(Decimal("0.01"))


def _band_for(case: ExceptionCase):
    """The approval band, from the control plane's own routing decision."""
    from nav_sentinel.domain.models import ApprovalClass

    decision = gateway.route_for_approval(case.to_facts())
    return ApprovalClass(decision.metadata["band"])


def _proposal_id(case: ExceptionCase, drafted: ProposalDraft) -> str:
    """Content-derived, so a re-run produces the same id -- S8a's byte-identical criterion."""
    import hashlib

    material = "|".join(
        [
            case.case_id,
            drafted.outcome,
            *(f"{x.account}:{x.currency}:{x.debit}:{x.credit}" for x in drafted.lines),
            *(f"{x.account}:{x.isin}:{x.from_quantity}:{x.to_quantity}" for x in drafted.quantity_lines),
        ]
    )
    return f"PROP-{hashlib.sha256(material.encode()).hexdigest()[:16]}"


def post(proposal: RemediationProposal, case: ExceptionCase, approval_ref: str | None) -> None:
    """Attempt to commit a proposal. Denied for every published agent, by design.

    This exists so the denial is a real code path rather than an assertion in a document. It is the
    only function in the fleet that would reach the ledger, and P-003 stops it: no published
    manifest holds posting authority, a mutated one is refused because the registry's models are
    frozen, and the approval reference is resolved against the store rather than believed.
    """
    gateway.authorize_posting(case.to_facts(), approval_ref)
    raise PolicyViolation(  # pragma: no cover - unreachable while no agent may post
        gateway.policies.PolicyDecision(
            effect=gateway.policies.Effect.DENY,
            policy_id="P-003-NO-AUTONOMOUS-POSTING",
            reason="reached the ledger path, which no published agent may do",
            agent_ref=identity.current().ref,
            resource=proposal.proposal_id,
        )
    )
