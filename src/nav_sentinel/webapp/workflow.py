"""The actions an analyst takes, driving the pipeline that already exists.

Nothing here reimplements a decision. Detection, materiality, triage, investigation, drafting,
approval and the posting refusal all live where they lived before; this module sequences them for an
operator and persists what each step produced, so a case can be opened tomorrow by someone who was
not here today.

**Persisting the verdict and the proposal is new**, and it closes a defect this project carried for
weeks: no runnable path stored either, so a case's established cause and its drafted correction died
with the process that produced them. An application cannot show an analyst what the fleet concluded
if the conclusion was never written down.

Writes go through the same authority as `make approve`. The console holds no privilege the CLI does
not: `ApprovalAuthority` is constructed by the composition root, posting is attempted through the
gateway, and P-003 refuses it afterwards exactly as before. What the web layer adds is a *named
analyst*, which is the one thing a service-to-service token cannot carry and the thing four-eyes
counts.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Any

from nav_sentinel import composition
from nav_sentinel.agents import contract, remediation, triage
from nav_sentinel.agents.investigator import investigate
from nav_sentinel.control_plane import audit, gateway
from nav_sentinel.control_plane.approvals import ApprovalDenied, Principal
from nav_sentinel.control_plane.governance import ApprovalClass
from nav_sentinel.domain import materiality, signals
from nav_sentinel.pipeline import cycle_runner
from nav_sentinel.registry import discover
from nav_sentinel.tools import books_and_records as bnr

DEFAULT_AS_OF = date(2026, 8, 17)


@dataclass(frozen=True)
class QueueItem:
    """One row of the exceptions queue, as an analyst reads it."""

    case_id: str
    as_of: str
    capability: str
    impact_bps: str
    band: str
    worked: bool
    approved: bool
    isin: str
    note: str
    title: str = "Exception"


def _cases(as_of: date):
    """Detect and score, which needs no model. The queue exists before any agent has run."""
    custodian = bnr.nav_record("custodian", "MERID-GEF", as_of)
    rates = cycle_runner._fixture_rates(as_of)
    detected = cycle_runner.detect(as_of)
    for case in detected:
        materiality.score(case, custodian, rates)
    return detected


def run_cycle(as_of: date = DEFAULT_AS_OF) -> list[QueueItem]:
    """Reconcile, score, band and persist the queue. No model is called.

    Deliberately model-free, and the page says so: detecting a break is arithmetic over two books
    and giving it to a model would be spending a request to be told what subtraction already knows.
    """
    store = composition.store()
    items: list[QueueItem] = []
    for case in _cases(as_of):
        facts = case.to_facts()
        with audit.case_trace(facts) as (_span, trace_id, band):
            existing = store.load_case(case.case_id) or {}
            document = {
                **existing,
                "case_id": case.case_id,
                "subject_id": facts.subject_id,
                "as_of": facts.as_of.isoformat(),
                "capability": facts.capability,
                "status": facts.status,
                "impact": str(facts.impact) if facts.impact else None,
                "impact_bps": str(case.nav_impact_bps) if case.nav_impact_bps is not None else "",
                "approval_band": band,
                "trace_id": trace_id,
                "isin": next((b.isin for b in case.breaks if b.isin), ""),
                # Break types, so a page can name the exception in an operator's language instead
                # of showing `nav.unclassified` -- an internal enum meaning "triage has not run".
                "break_types": [b.break_type.value for b in case.breaks],
                "note": next((b.note for b in case.breaks if b.note), ""),
            }
            store.save_case(case.case_id, document)
        items.append(_to_item(document))
    return items


def _to_item(document: dict[str, Any]) -> QueueItem:
    return QueueItem(
        case_id=str(document.get("case_id", "")),
        as_of=str(document.get("as_of", "")),
        capability=str(document.get("capability", "")),
        impact_bps=str(document.get("impact_bps") or ""),
        band=str(document.get("approval_band", "")),
        worked=bool(document.get("verdict")),
        approved=bool(document.get("approval_ref")),
        isin=str(document.get("isin", "")),
        note=str(document.get("note", "")),
        title=_title(document),
    )


def _title(document: dict[str, Any]) -> str:
    from nav_sentinel.webapp.pages import describe

    return describe(document)


def queue(as_of: date = DEFAULT_AS_OF) -> list[QueueItem]:
    """The queue as persisted. Empty until a cycle has been run, which is the honest state."""
    store = composition.store()
    rows = [
        store.load_case(case.case_id)
        for case in _cases(as_of)
    ]
    return [_to_item(r) for r in rows if r]


def case_detail(case_id: str, as_of: date = DEFAULT_AS_OF) -> dict[str, Any]:
    """Everything the case page shows, assembled from the store and the books."""
    store = composition.store()
    document = store.load_case(case_id) or {}
    case = next((c for c in _cases(as_of) if c.case_id == case_id), None)
    detail: dict[str, Any] = {
        "document": document,
        "case": case,
        "observations": store.observations_for(case_id),
        "decisions": store.decisions_for(case_id),
        "signals": [],
    }
    if case is not None:
        with gateway.identity.acting_as("triage-agent"):
            detail["signals"] = list(signals.for_case(case))
    return detail


def work_case(case_id: str, as_of: date = DEFAULT_AS_OF) -> dict[str, Any]:
    """Triage, route, investigate and draft. **This is the step that calls models.**

    Sequenced exactly as `make investigate` does, including the outcomes that are not successes: an
    unrouted capability stops here and a verdict that establishes no cause is not drafted against.
    """
    store = composition.store()
    case = next((c for c in _cases(as_of) if c.case_id == case_id), None)
    if case is None:
        raise LookupError(f"{case_id} is not a case in the {as_of.isoformat()} cycle")

    classification = asyncio.run(triage.classify(case, discover.get("triage-agent")))
    case.category = contract.category_for(classification.capability)
    facts = case.to_facts()

    document = dict(store.load_case(case_id) or {})
    document["capability"] = facts.capability
    document["triage"] = {
        "capability": classification.capability,
        "confidence": classification.confidence,
        "reasoning": classification.reasoning,
        "overridden_from": classification.overridden_from,
    }

    agent = (
        discover.discover_for_capability(facts.capability)
        if classification.classified
        else None
    )
    if agent is None:
        document["routed"] = False
        document["refusal"] = (
            f"no published agent handles {facts.capability}, so this case escalates to a human"
        )
        store.save_case(case_id, document)
        return document

    document["routed"] = True
    document["investigator"] = agent.ref

    with audit.case_trace(facts) as (_span, trace_id, band):
        verdict, observations = asyncio.run(
            investigate(case.to_brief(), agent, trace_id=trace_id)
        )
        for observation in observations.as_mapping().values():
            store.record_observation(observation)
        document["verdict"] = {
            "root_cause": verdict.root_cause,
            "confidence": verdict.confidence,
            "citations": [c.observation_id for c in verdict.citations],
            "unresolved": verdict.unresolved,
            "agent": agent.ref,
        }
        document["approval_band"] = band

        if verdict.asserts_a_cause:
            proposal = asyncio.run(
                remediation.draft(
                    case, verdict, discover.get("remediation-agent"), trace_id=trace_id
                )
            )
            document["proposal"] = {
                "proposal_id": proposal.proposal_id,
                "outcome": proposal.outcome.value,
                "rationale": proposal.rationale,
                "expected_residual": str(proposal.expected_residual),
                "requires": proposal.requires.value,
                "lines": [
                    {
                        "account": line.account,
                        "currency": line.currency,
                        "debit": str(line.debit) if line.debit else "",
                        "credit": str(line.credit) if line.credit else "",
                        "narrative": line.narrative,
                    }
                    for line in proposal.lines
                ],
                "quantity_lines": [
                    {
                        "account": q.account,
                        "isin": q.isin,
                        "from_quantity": str(q.from_quantity),
                        "to_quantity": str(q.to_quantity),
                    }
                    for q in proposal.quantity_lines
                ],
            }
        for sequence, decision in enumerate(gateway.decisions_since(case_id)):
            store.record_decision(case_id, trace_id, sequence, decision)

    store.save_case(case_id, document)
    return document


@dataclass(frozen=True)
class ApprovalOutcome:
    """What happened when an analyst signed, and what the system did next."""

    granted: bool
    message: str
    outstanding: int
    posting_refused: str = ""


def approve(
    case_id: str, principal: Principal, *, as_of: date = DEFAULT_AS_OF
) -> ApprovalOutcome:
    """Record one analyst's signature, then attempt to post and be refused.

    The second half is the point. An approval is *necessary and not sufficient*: no published agent
    holds posting authority, so P-003 denies the attempt whatever signatures exist. Doing it here
    rather than describing it means the refusal is on screen rather than in a paragraph.
    """
    store = composition.store()
    document = dict(store.load_case(case_id) or {})
    if not document:
        raise LookupError(case_id)
    band = ApprovalClass(document.get("approval_band", "single_reviewer"))

    signed: list[str] = list(document.get("signed_by", []))
    roles: list[str] = list(document.get("signed_roles", []))

    # An ineligible signature is **not recorded**. It is not a partial signature; it is not a
    # signature. Recording it first poisoned every later attempt: a reviewer's role stayed in the
    # list, so two controllers signing afterwards were still refused on role -- the authority
    # answering correctly about a record the application should never have built.
    allowed, required = _requirement(band)
    if principal.role not in allowed:
        return ApprovalOutcome(
            granted=False,
            message=(
                f"{principal} may not sign at {band.value}; it requires "
                f"{' or '.join(sorted(allowed))}. Nothing was recorded."
            ),
            outstanding=max(0, required - len(set(signed))),
        )

    if principal.subject not in signed:
        signed.append(principal.subject)
        roles.append(principal.role)

    authority = composition.approval_authority()
    principals = tuple(
        Principal(subject=s, role=r) for s, r in zip(signed, roles, strict=True)
    )
    document["signed_by"] = signed
    document["signed_roles"] = roles

    try:
        record = authority.grant(case_id, band, principals, note="approved in the console")
    except ApprovalDenied as denied:
        store.save_case(case_id, document)
        _allowed, required = _requirement(band)
        return ApprovalOutcome(
            granted=False,
            message=str(denied),
            outstanding=max(0, required - len(set(signed))),
        )

    document["approval_ref"] = record.ref
    store.save_case(case_id, document)
    return ApprovalOutcome(
        granted=True,
        message=f"{record.ref} granted at {band.value} by {', '.join(record.approvers)}",
        outstanding=0,
        posting_refused=_attempt_posting(case_id, record.ref, as_of=as_of),
    )


def _requirement(band: ApprovalClass) -> tuple[frozenset[str], int]:
    from nav_sentinel.control_plane.approvals import BAND_REQUIREMENTS

    return BAND_REQUIREMENTS[band]


def _attempt_posting(case_id: str, approval_ref: str, *, as_of: date) -> str:
    """Post the approved correction, and report the refusal.

    The approval reference is passed in, so this is not a straw attempt: a *valid, resolvable*
    signature is presented and P-003 refuses anyway, because no published agent holds posting
    authority. An attempt without the reference would be refused for the wrong reason and would
    prove nothing.
    """
    case = next((c for c in _cases(as_of) if c.case_id == case_id), None)
    if case is None:
        return ""
    try:
        with gateway.identity.acting_as("remediation-agent"):
            gateway.authorize_posting(case.to_facts(), approval_ref)
    except Exception as refused:  # noqa: BLE001
        return str(refused)
    return "posting was NOT refused — investigate immediately"
