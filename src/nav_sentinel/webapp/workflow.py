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
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from queue import Queue
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
        # Marked before the trace, so persisting this case does not re-persist the previous one's.
        # `cycle_runner.run` has always done this and the desk never did, so a cycle started from
        # the browser derived P-004 for every case and recorded it nowhere -- two entry points to
        # the same work, one of them keeping the governance log and one dropping it.
        gateway.mark_decisions(case.case_id)
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
            for sequence, decision in enumerate(gateway.decisions_since(case.case_id)):
                store.record_decision(case.case_id, trace_id, sequence, decision)
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
    """Triage, route, investigate and draft, reporting after each stage. **This calls models.**

    Sequenced exactly as `make investigate` does, including the outcomes that are not successes: an
    unrouted capability stops here and a verdict that establishes no cause is not drafted against.

    `work_case` drives this same function to completion, so the streaming desk and every
    non-streaming caller run one implementation. Two would drift, and the one that drifted would be
    the one nothing watches.

    **The work runs on its own thread and the events arrive over a queue.** The obvious shape --
    one generator that holds the trace span open across its `yield`s -- does not survive contact
    with the server: Starlette drives a sync generator through a thread pool, so each `next()` can
    land on a different thread with a different context, and OpenTelemetry then cannot detach the
    span token it attached (`Token was created in a different Context`). Confirmed on the deployed
    service, in the logs, on the first real run. Keeping the span inside one thread fixes that, and
    has a second property worth having: a model call already in flight and already billed finishes
    and is recorded even if the analyst closes the tab.
    """
    # `Queue`, not `import queue`: this module already exports a function called `queue`, and
    # importing the stdlib module of that name shadowed it.
    events: Queue[WorkEvent | None | BaseException] = Queue()

    def run() -> None:
        try:
            _work(case_id, as_of, events.put)
        except BaseException as failed:  # noqa: BLE001 -- handed to the consumer, not swallowed
            events.put(failed)
        finally:
            events.put(None)

    worker = threading.Thread(target=run, name=f"work-{case_id}", daemon=True)
    worker.start()
    while True:
        item = events.get()
        if item is None:
            return
        if isinstance(item, BaseException):
            raise item
        yield item


def _work(
    case_id: str, as_of: date, emit: Callable[[WorkEvent], None]
) -> None:
    """The staged investigation itself, on one thread, reporting through `emit`."""
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

    emit(WorkEvent("triage", "running", store.load_case(case_id) or {}))
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
    emit(WorkEvent("triage", "done", document))

    emit(WorkEvent("routing", "running", document))
    agent = (
        discover.discover_for_capability(facts.capability)
        if classification.classified
        else None
    )
    # Recorded before the branch, so the refusal is in the governance log on the path that returns
    # early. It used to persist nothing: the most interesting thing the registry does was a field on
    # a rewritable case document while every successful routing left governed tool calls behind it.
    routing = gateway.record_capability_routing(
        case_id, facts.capability, agent.ref if agent else None
    )
    store.record_decision(case_id, None, _routing_sequence(store, case_id), routing)

    if agent is None:
        document = patch(routed=False, refusal=routing.reason)
        emit(WorkEvent("routing", "refused", document, detail=routing.reason))
        return

    document = patch(routed=True, investigator=agent.ref)
    emit(WorkEvent("routing", "done", document, detail=agent.ref))

    with audit.case_trace(facts) as (_span, trace_id, band):
        try:
            emit(WorkEvent("investigation", "running", document))
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
            emit(WorkEvent("investigation", "done", document, detail=agent.ref))

            if not verdict.asserts_a_cause:
                emit(WorkEvent(
                    "proposal",
                    "skipped",
                    document,
                    detail="no cause was established, so nothing is drafted against it",
                ))
                return

            emit(WorkEvent("proposal", "running", document))
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
            emit(WorkEvent("proposal", "done", document))
        finally:
            # In a `finally` because the consumer can abandon this generator -- a closed browser
            # tab closes the stream, which throws `GeneratorExit` in here. The gateway's decisions
            # are the record of what was allowed and refused on the way, and they must survive a
            # disconnect: a governance trail that only persists on the happy path is not one.
            for sequence, decision in enumerate(gateway.decisions_since(case_id)):
                store.record_decision(case_id, trace_id, sequence, decision)


def work_case(case_id: str, as_of: date = DEFAULT_AS_OF) -> dict[str, Any]:
    """Work a case to completion and return the final document.

    Calls `_work` directly rather than draining `work_case_events`: there is no consumer to report
    to, so the thread and the queue would be a hop that buys nothing -- and this keeps the trace
    span on the calling thread, which is where every non-streaming caller already expects it.
    """
    document: dict[str, Any] = {}

    def keep(event: WorkEvent) -> None:
        nonlocal document
        document = event.document

    _work(case_id, as_of, keep)
    return document


@dataclass(frozen=True)
class ApprovalOutcome:
    """What happened when an analyst signed, and what the system did next."""

    granted: bool
    message: str
    outstanding: int
    #: Why no agent could post this, once it was approved. Empty until it is.
    agent_posting_blocked: str = ""


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
        agent_posting_blocked=_confirm_no_agent_can_post(case_id, record.ref, as_of=as_of),
    )


#: The stages a case walks, and how each one is read back out of the store. Derived from the
#: persisted document, never from anything held in memory by whichever instance did the work --
#: with Pub/Sub fan-out the browser is not talking to that instance, and Firestore is the only
#: thing both of them can see.
LIVE_STAGES: tuple[tuple[str, str], ...] = (
    ("triage", "Triage"),
    ("routing", "Route"),
    ("investigation", "Investigate"),
    ("draft", "Draft"),
)


def _next_step(document: dict[str, Any]) -> tuple[str, str]:  # noqa: PLR0911
    """What has to happen next, and who has to do it.

    The fleet finishing is not the case finishing, and a screen that showed four green ticks and
    stopped would imply otherwise. Every one of these is a person's move: the whole claim of this
    system is that the last step is never the agent's.
    """
    from nav_sentinel.control_plane.approvals import BAND_REQUIREMENTS
    from nav_sentinel.control_plane.governance import ApprovalClass

    if document.get("approval_ref"):
        return "posted_by_ledger", "Cleared — release to the ledger"
    if document.get("routed") is False:
        return "human_investigation", "No authorised agent — investigate by hand"
    if not document.get("verdict"):
        return "fleet", "Fleet working"
    if not document.get("proposal"):
        return "human_investigation", "No cause established — investigate by hand"

    band = str(document.get("approval_band", "single_reviewer"))
    try:
        allowed, required = BAND_REQUIREMENTS[ApprovalClass(band)]
    except (KeyError, ValueError):
        return "sign", "Awaiting signature"
    outstanding = max(0, required - len(set(document.get("signed_by", []))))
    if outstanding == 0:
        return "sign", "Awaiting signature"
    # "cio, controller or reviewer" rather than "cio or controller or reviewer".
    names = sorted(allowed)
    who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" or {names[-1]}"
    return "sign", (
        f"Needs {outstanding} more signature{'s' if outstanding > 1 else ''} from {who}"
    )


def _stage_states(document: dict[str, Any]) -> dict[str, str]:
    """Where this case has got to, read from what is stored."""
    routed = document.get("routed")
    return {
        "triage": "done" if document.get("triage") else "pending",
        "routing": "done" if routed else ("refused" if routed is False else "pending"),
        "investigation": "done"
        if document.get("verdict")
        else ("blocked" if routed is False else "pending"),
        "draft": "done"
        if document.get("proposal")
        else ("blocked" if routed is False else "pending"),
    }


def live_snapshot(
    as_of: date = DEFAULT_AS_OF, *, since: str = "", feed: int = 40
) -> dict[str, Any]:
    """What the fleet is doing, assembled from the store.

    Every number here is counted from a persisted record. That is not a stylistic preference: a
    counter derived from anything else is the defect this project keeps hitting -- a display that
    reports work nobody can go and check.

    `since` scopes the counters and the feed to one run. Without it they open at the accumulated
    total of every rehearsal, because `demo-reset` deliberately preserves decisions and observations
    -- a true number answering a question nobody asked.
    """
    store = composition.store()
    documents = [store.load_case(case.case_id) or {} for case in _cases(as_of)]

    rows: list[dict[str, Any]] = []
    agents: set[str] = set()
    evidence = 0
    for document in documents:
        case_id = str(document.get("case_id", ""))
        agent = document.get("investigator") or (document.get("verdict") or {}).get("agent")
        if agent:
            agents.add(str(agent))
        observations = [
            observation
            for observation in store.observations_for(case_id)
            if not since or observation.retrieved_at.isoformat() >= since
        ]
        evidence += len(observations)
        rows.append(
            {
                "case_id": case_id,
                "title": _title(document),
                "band": str(document.get("approval_band", "")),
                "impact_bps": str(document.get("impact_bps") or ""),
                "capability": str(document.get("capability", "")),
                "agent": str(agent or ""),
                "refusal": str(document.get("refusal") or ""),
                "approved": bool(document.get("approval_ref")),
                "stages": _stage_states(document),
                "evidence": len(observations),
                "next_kind": _next_step(document)[0],
                "next_step": _next_step(document)[1],
            }
        )

    decisions = store.recent_decisions(feed, since=since)
    investigated = sum(1 for r in rows if r["stages"]["investigation"] == "done")
    refused = sum(1 for r in rows if r["stages"]["routing"] == "refused")
    return {
        "as_of": as_of.isoformat(),
        "since": since,
        "stages": [{"key": key, "label": label} for key, label in LIVE_STAGES],
        "cases": rows,
        "counters": {
            "cases": len(rows),
            "investigated": investigated,
            "refused": refused,
            "agents": len(agents),
            "tool_calls": sum(
                1 for d in decisions if str(d.get("nav.policy.id", "")).startswith("P-001")
            ),
            "evidence": evidence,
            "decisions": len(decisions),
            "denials": sum(1 for d in decisions if d.get("nav.policy.effect") == "deny"),
        },
        # Terminal means nothing more will change without someone acting. The page stops polling
        # then and says so, rather than asking Firestore the same question every second forever.
        "handover": {
            "sign": sum(1 for r in rows if r["next_kind"] == "sign"),
            "human_investigation": sum(
                1 for r in rows if r["next_kind"] == "human_investigation"
            ),
            "fleet": sum(1 for r in rows if r["next_kind"] == "fleet"),
            "posted_by_ledger": sum(1 for r in rows if r["next_kind"] == "posted_by_ledger"),
        },
        "settled": all(
            r["stages"]["draft"] in ("done", "blocked")
            and r["stages"]["investigation"] in ("done", "blocked")
            for r in rows
        )
        and bool(rows),
        "feed": [
            {
                "at": str(d.get("recorded_at", ""))[11:19],
                "effect": str(d.get("nav.policy.effect", "")),
                "policy": str(d.get("nav.policy.id", "")),
                "reason": str(d.get("nav.policy.reason", "")),
                "agent": str(d.get("nav.agent.ref", "") or ""),
            }
            for d in decisions
        ],
    }


def _routing_sequence(store, case_id: str) -> int:
    """The next free position in this case's decision log.

    The log is keyed by (case, trace, sequence) and refuses a duplicate, so a routing decision
    written outside the investigation trace has to claim a position no earlier write took --
    including on a re-run, which is a real path now that re-working a case is supported.
    """
    return len([d for d in store.decisions_for(case_id) if d.get("trace_id") in (None, "")]) + 1000


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


def _confirm_no_agent_can_post(case_id: str, approval_ref: str, *, as_of: date) -> str:
    """Check, at the moment of approval, that no agent can post the thing just approved.

    Named for what it is. It used to be `_attempt_posting`, and the desk reported its result to the
    analyst as "Posting refused" -- which reads as *your action failed*, when the analyst never
    asked to post anything. They asked to approve, and the approval succeeded. What this establishes
    is a property of the system: the correction is cleared, and it leaves the fleet's hands here.

    The approval reference is passed in, so this is not a straw check: a *valid, resolvable*
    signature is presented under an agent's identity and P-003 refuses anyway, because no published
    agent holds posting authority. A check without the reference would be refused for the wrong
    reason and would prove nothing.
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
