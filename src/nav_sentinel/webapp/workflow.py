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
from collections.abc import Iterator
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


@dataclass(frozen=True)
class WorkEvent:
    """One step of the investigation, as it happens.

    The desk streams these so an analyst watching a case being worked sees triage land, then the
    routing decision, then the cause, then the draft -- rather than a frozen page and then
    everything at once. Each stage also **persists as it completes**, so what the stream showed and
    what a refresh shows cannot disagree, and a dropped connection leaves the work done so far
    recorded rather than lost.
    """

    stage: str
    state: str
    document: dict[str, Any]
    detail: str = ""


#: The stages, in order, with the label an analyst reads. Declared rather than inferred so the
#: progress list can be drawn complete-but-pending before any of it has happened -- a progress
#: indicator that grows as it goes cannot show how much is left.
WORK_STAGES: tuple[tuple[str, str], ...] = (
    ("triage", "Classify the difference"),
    ("routing", "Find the authorised agent"),
    ("investigation", "Investigate and cite evidence"),
    ("proposal", "Draft the correcting entry"),
)


def work_case_events(case_id: str, as_of: date = DEFAULT_AS_OF) -> Iterator[WorkEvent]:
    """Triage, route, investigate and draft, yielding after each stage. **This calls models.**

    Sequenced exactly as `make investigate` does, including the outcomes that are not successes: an
    unrouted capability stops here and a verdict that establishes no cause is not drafted against.

    `work_case` drives this same generator to completion, so the streaming desk and every
    non-streaming caller run one implementation. Two would drift, and the one that drifted would be
    the one nothing watches.
    """
    store = composition.store()
    case = next((c for c in _cases(as_of) if c.case_id == case_id), None)
    if case is None:
        raise LookupError(f"{case_id} is not a case in the {as_of.isoformat()} cycle")

    def patch(**fields: Any) -> dict[str, Any]:
        """Merge fields into the stored document atomically and return what is now stored.

        `update_case` rather than a whole-document write: an analyst may be signing this case while
        it is being re-worked, and a blind `set()` would drop their signature.
        """
        return store.update_case(case_id, lambda document: {**document, **fields})

    yield WorkEvent("triage", "running", store.load_case(case_id) or {})
    classification = asyncio.run(triage.classify(case, discover.get("triage-agent")))
    case.category = contract.category_for(classification.capability)
    facts = case.to_facts()
    document = patch(
        capability=facts.capability,
        triage={
            "capability": classification.capability,
            "confidence": classification.confidence,
            "reasoning": classification.reasoning,
            "overridden_from": classification.overridden_from,
        },
    )
    yield WorkEvent("triage", "done", document)

    yield WorkEvent("routing", "running", document)
    agent = (
        discover.discover_for_capability(facts.capability)
        if classification.classified
        else None
    )
    if agent is None:
        refusal = (
            f"no published agent handles {facts.capability}, so this case escalates to a human"
        )
        document = patch(routed=False, refusal=refusal)
        yield WorkEvent("routing", "refused", document, detail=refusal)
        return

    document = patch(routed=True, investigator=agent.ref)
    yield WorkEvent("routing", "done", document, detail=agent.ref)

    with audit.case_trace(facts) as (_span, trace_id, band):
        try:
            yield WorkEvent("investigation", "running", document)
            verdict, observations = asyncio.run(
                investigate(case.to_brief(), agent, trace_id=trace_id)
            )
            for observation in observations.as_mapping().values():
                store.record_observation(observation)
            document = patch(
                verdict={
                    "root_cause": verdict.root_cause,
                    "confidence": verdict.confidence,
                    "citations": [c.observation_id for c in verdict.citations],
                    "unresolved": verdict.unresolved,
                    "agent": agent.ref,
                },
                approval_band=band,
            )
            yield WorkEvent("investigation", "done", document, detail=agent.ref)

            if not verdict.asserts_a_cause:
                yield WorkEvent(
                    "proposal",
                    "skipped",
                    document,
                    detail="no cause was established, so nothing is drafted against it",
                )
                return

            yield WorkEvent("proposal", "running", document)
            proposal = asyncio.run(
                remediation.draft(
                    case, verdict, discover.get("remediation-agent"), trace_id=trace_id
                )
            )
            document = patch(
                proposal={
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
            )
            yield WorkEvent("proposal", "done", document)
        finally:
            # In a `finally` because the consumer can abandon this generator -- a closed browser
            # tab closes the stream, which throws `GeneratorExit` in here. The gateway's decisions
            # are the record of what was allowed and refused on the way, and they must survive a
            # disconnect: a governance trail that only persists on the happy path is not one.
            for sequence, decision in enumerate(gateway.decisions_since(case_id)):
                store.record_decision(case_id, trace_id, sequence, decision)


def work_case(case_id: str, as_of: date = DEFAULT_AS_OF) -> dict[str, Any]:
    """Work a case to completion and return the final document. Drives `work_case_events`."""
    document: dict[str, Any] = {}
    for event in work_case_events(case_id, as_of):
        document = event.document
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
    refusal: list[str] = []

    def _countersign(document: dict) -> dict:
        """Add this analyst's signature, atomically, if it is one this case can accept.

        Runs inside `update_case`, so two controllers signing the same case at the same moment
        both land. Read-modify-write lost one of them, silently -- and four-eyes is the one control
        that *guarantees* two people are on a case at once.

        Re-run on a Firestore retry, so it must be a pure function of the document handed to it.
        """
        band = ApprovalClass(document.get("approval_band", "single_reviewer"))
        signed: list[str] = list(document.get("signed_by", []))
        roles: list[str] = list(document.get("signed_roles", []))

        # A signature is given for a *specific correction at a specific band*, so it is void when
        # either changes. Without this, re-working a case swapped the journal entry underneath
        # signatures that stayed valid -- two controllers on record as having approved a
        # correction neither of them ever saw. The band matters for the same reason in reverse:
        # signatures collected toward four-eyes must not carry over and satisfy a lower band alone.
        if document.get("signed_for") != _signed_for(document):
            signed, roles = [], []
            document.pop("approval_ref", None)

        allowed, required = _requirement(band)
        if principal.role not in allowed:
            # An ineligible signature is **not recorded**. It is not a partial signature; it is not
            # a signature. Recording it first poisoned every later attempt: a reviewer's role stayed
            # in the list, so two controllers signing afterwards were still refused on role -- the
            # authority answering correctly about a record the application should never have built.
            refusal.append(
                f"{principal} may not sign at {band.value}; it requires "
                f"{' or '.join(sorted(allowed))}. Nothing was recorded."
            )
        elif principal.subject not in signed:
            signed.append(principal.subject)
            roles.append(principal.role)

        document["signed_by"] = signed
        document["signed_roles"] = roles
        document["signed_for"] = _signed_for(document)
        return document

    document = store.update_case(case_id, _countersign)
    band = ApprovalClass(document.get("approval_band", "single_reviewer"))
    signed = list(document.get("signed_by", []))
    roles = list(document.get("signed_roles", []))
    _allowed, required = _requirement(band)

    if refusal:
        return ApprovalOutcome(
            granted=False, message=refusal[-1], outstanding=max(0, required - len(set(signed)))
        )

    authority = composition.approval_authority()
    # `strict=False`: a case document written before signatures carried roles would otherwise raise
    # on length mismatch and 500 the page. Firestore documents outlive the deploy that wrote them.
    principals = tuple(Principal(subject=s, role=r) for s, r in zip(signed, roles, strict=False))

    try:
        record = authority.grant(case_id, band, principals, note="approved in the console")
    except ApprovalDenied as denied:
        return ApprovalOutcome(
            granted=False,
            message=str(denied),
            outstanding=max(0, required - len(set(signed))),
        )

    store.update_case(case_id, lambda d: {**d, "approval_ref": record.ref})
    return ApprovalOutcome(
        granted=True,
        message=f"{record.ref} granted at {band.value} by {', '.join(record.approvers)}",
        outstanding=0,
        posting_refused=_attempt_posting(case_id, record.ref, as_of=as_of),
    )


def _signed_for(document: dict) -> str:
    """What a signature on this document would be a signature *for*.

    The band and the proposal, because those are the two things an approver is actually agreeing
    to. When either moves, the signatures collected against the old one are void.
    """
    band = document.get("approval_band", "single_reviewer")
    proposal_id = (document.get("proposal") or {}).get("proposal_id", "")
    return f"{band}|{proposal_id}"


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
