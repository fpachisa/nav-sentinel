"""Where a cycle's work is kept, so it survives the process that did it.

Everything the fleet produces has lived in memory: the governance log in a ContextVar, observations
in a per-case dict, verdicts on an `ExceptionCase` object. That is correct for one request and
useless the moment there are two. Cloud Run scales to zero and runs several instances, so a case
worked on one instance is invisible to the next -- and the audit trail is the deliverable, not a
by-product of a process that happens to still be running.

Three collections, and the difference between them matters:

**Cases and proposals are documents that change.** A case is opened, classified, investigated,
proposed against, approved, closed. Writing the current state is right.

**Policy decisions are append-only.** A governance log you can edit is not one. Every decision is a
new document keyed by `case_id`, `trace_id` and its position in the sequence, so two writers cannot
overwrite each other and a replay reconstructs the order. The store refuses an id that already
exists rather than overwriting -- the same rule approvals already follow, for the same reason.

**Observations are append-only and immutable.** They are what a citation resolves against, so a
mutable observation would let evidence be rewritten after the verdict that cited it.

The interface is deliberately narrow and synchronous. Firestore's client is synchronous, the
handlers are synchronous, and an async repository would add a moving part to buy nothing.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from nav_sentinel.config import settings
from nav_sentinel.control_plane.observations import Observation

if TYPE_CHECKING:  # pragma: no cover

    from nav_sentinel.control_plane.policies import PolicyDecision


#: Bound when the Firestore client is first imported. The positional `where()` form is deprecated
#: and warns on every call; the keyword form needs this type, and importing it at module scope would
#: pull the Google SDK into every offline run.
FieldFilter: Any = None


class RepositoryUnavailable(RuntimeError):
    """The backing store could not be reached. Fails closed rather than degrading silently.

    Degrading to memory on a Firestore outage would mean the audit trail quietly stopped being
    written while everything appeared to work -- which is the failure mode this whole project keeps
    finding and closing.
    """


class ImmutableRecord(ValueError):
    """An append-only record already exists under that id."""


class Repository(ABC):
    """What a cycle needs to persist. Nothing here knows what a NAV is."""

    # --- cases: current state -------------------------------------------------------------
    @abstractmethod
    def save_case(self, case_id: str, document: dict[str, Any]) -> None: ...

    @abstractmethod
    def load_case(self, case_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def update_case(
        self, case_id: str, mutate: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> dict[str, Any]:
        """Read, change and write a case document atomically. Returns what was stored.

        `save_case` is a blind whole-document write, which is correct for the pipeline that owns a
        case for the length of one run and wrong for the console, where two analysts act on the same
        case at the same time. Four-eyes *requires* two people on one case; the load-mutate-save it
        was built on lost whichever signature landed first, silently, because Firestore has no
        opinion about a `set()` over a document that moved underneath it.

        Fails safe -- a lost update drops a signature rather than inventing one -- which is exactly
        why nothing caught it: the suite is single-threaded and never has two requests in flight.
        """

    @abstractmethod
    def cases_for(self, subject_id: str, as_of: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def cases_by_recurrence(self, recurrence_key: str) -> list[dict[str, Any]]: ...

    # --- stage history: append-only, ordered ----------------------------------------------
    @abstractmethod
    def record_stage(self, case_id: str, sequence: int, entry: dict[str, Any]) -> None: ...

    @abstractmethod
    def stages_for(self, case_id: str) -> list[dict[str, Any]]: ...

    # --- observations: append-only, immutable ---------------------------------------------
    @abstractmethod
    def record_observation(self, observation: Observation) -> None: ...

    @abstractmethod
    def observations_for(self, case_id: str) -> list[Observation]: ...

    # --- policy decisions: append-only, ordered ------------------------------------------
    @abstractmethod
    def record_decision(
        self, case_id: str, trace_id: str | None, sequence: int, decision: PolicyDecision
    ) -> None: ...

    @abstractmethod
    def decisions_for(self, case_id: str) -> list[dict[str, Any]]: ...


def _decision_id(case_id: str, trace_id: str | None, sequence: int) -> str:
    """Keyed by case, trace and position.

    The sequence is part of the id rather than a field so ordering survives a store with no
    guaranteed read order, and so two instances writing the same case cannot silently overwrite one
    another -- they collide, and a collision on an append-only record is an error worth raising.
    """
    return f"{case_id}|{trace_id or 'no-trace'}|{sequence:04d}"


def _stage_id(case_id: str, sequence: int) -> str:
    """Keyed by case and position, and that is the concurrency control.

    A case at stage N has exactly one stage N+1. Two workers advancing the same case both write
    sequence N+1, one collides, and a collision on an append-only record raises rather than
    overwrites -- so a case cannot be double-advanced by two Pub/Sub deliveries of the same event.
    Pub/Sub is at-least-once, so that is not a hypothetical.
    """
    return f"{case_id}|{sequence:04d}"


def _decision_order(record: dict[str, Any]) -> tuple[str, int]:
    """Group a case's decisions by the run that produced them, then by position within it.

    Re-running a cycle is a second investigation, not a correction of the first, so both runs' logs
    are kept -- the trail would be editable otherwise. Ordering by sequence alone interleaved them:
    measured against the live database, two runs of one case read back as
    `[0, 0, 1, 1, 2, 2, 3, 3]`, which reads as one confused sequence rather than two clean ones.
    """
    return (record.get("trace_id") or "", record.get("sequence", 0))


class InMemoryRepository(Repository):
    """For tests and offline runs. Enforces the same append-only rules as Firestore.

    Enforcing them here is the point: a memory store that quietly allowed overwrites would let the
    offline suite pass while the deployed service raised, so the rules would only ever be tested in
    production.
    """

    def __init__(self) -> None:
        # A real lock, not a comment saying single-threaded. The offline suite is, and the
        # threaded test that proves the Firestore path is not; both run against this class.
        self._lock = threading.Lock()
        self._cases: dict[str, dict[str, Any]] = {}
        self._observations: dict[str, Observation] = {}
        self._decisions: dict[str, dict[str, Any]] = {}
        self._stages: dict[str, dict[str, Any]] = {}

    def save_case(self, case_id: str, document: dict[str, Any]) -> None:
        self._cases[case_id] = dict(document)

    def load_case(self, case_id: str) -> dict[str, Any] | None:
        found = self._cases.get(case_id)
        return dict(found) if found else None

    def update_case(
        self, case_id: str, mutate: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> dict[str, Any]:
        with self._lock:
            document = self._cases.get(case_id)
            if document is None:
                raise LookupError(case_id)
            updated = mutate(dict(document))
            self._cases[case_id] = dict(updated)
            return dict(updated)

    def cases_for(self, subject_id: str, as_of: str) -> list[dict[str, Any]]:
        return [
            dict(document)
            for document in self._cases.values()
            if document.get("subject_id") == subject_id and document.get("as_of") == as_of
        ]

    def cases_by_recurrence(self, recurrence_key: str) -> list[dict[str, Any]]:
        return [
            dict(document)
            for document in self._cases.values()
            if document.get("recurrence_key") == recurrence_key
        ]

    def record_stage(self, case_id: str, sequence: int, entry: dict[str, Any]) -> None:
        stage_id = _stage_id(case_id, sequence)
        if stage_id in self._stages:
            raise ImmutableRecord(
                f"stage {sequence} of {case_id} already recorded as "
                f"{self._stages[stage_id].get('to')!r}. A case has one stage at each position."
            )
        self._stages[stage_id] = {"case_id": case_id, "sequence": sequence, **entry}

    def stages_for(self, case_id: str) -> list[dict[str, Any]]:
        return sorted(
            (dict(e) for e in self._stages.values() if e["case_id"] == case_id),
            key=lambda e: e["sequence"],
        )

    def record_observation(self, observation: Observation) -> None:
        existing = self._observations.get(observation.observation_id)
        if existing is not None:
            if existing != observation:
                raise ImmutableRecord(
                    f"observation {observation.observation_id} already exists with different "
                    f"content. Ids are content-derived, so this means the derivation changed."
                )
            return  # the same call recorded twice is one observation, not an error
        self._observations[observation.observation_id] = observation

    def observations_for(self, case_id: str) -> list[Observation]:
        return [o for o in self._observations.values() if o.case_id == case_id]

    def record_decision(
        self, case_id: str, trace_id: str | None, sequence: int, decision: PolicyDecision
    ) -> None:
        key = _decision_id(case_id, trace_id, sequence)
        if key in self._decisions:
            raise ImmutableRecord(
                f"a policy decision already exists at {key}. The governance log is append-only; "
                f"overwriting one would make the trail editable."
            )
        self._decisions[key] = {
            "case_id": case_id,
            "trace_id": trace_id,
            "sequence": sequence,
            **decision.as_span_attributes(),
        }

    def decisions_for(self, case_id: str) -> list[dict[str, Any]]:
        return sorted(
            (r for r in self._decisions.values() if r["case_id"] == case_id), key=_decision_order
        )

    def clear(self) -> None:
        self._cases.clear()
        self._observations.clear()
        self._decisions.clear()


class FirestoreRepository(Repository):
    """Firestore native. Collections are prefixed so two deployments can share a project."""

    def __init__(self) -> None:
        try:
            from google.cloud import firestore
            from google.cloud.firestore_v1.base_query import FieldFilter as _FieldFilter
        except ImportError as exc:  # pragma: no cover
            raise RepositoryUnavailable(f"the Firestore client is not installed: {exc}") from exc

        global FieldFilter
        FieldFilter = _FieldFilter

        s = settings()
        try:
            self._client = firestore.Client(project=s.project)
        except Exception as exc:  # noqa: BLE001
            raise RepositoryUnavailable(
                f"cannot reach Firestore for project {s.project!r}: {exc}"
            ) from exc
        prefix = s.firestore_collection_prefix
        self._cases = self._client.collection(f"{prefix}_cases")
        self._observation_docs = self._client.collection(f"{prefix}_observations")
        self._decision_docs = self._client.collection(f"{prefix}_decisions")
        self._stage_docs = self._client.collection(f"{prefix}_stages")

    def save_case(self, case_id: str, document: dict[str, Any]) -> None:
        self._cases.document(case_id).set(document)

    def load_case(self, case_id: str) -> dict[str, Any] | None:
        doc = self._cases.document(case_id).get()
        return doc.to_dict() if doc.exists else None

    def update_case(
        self, case_id: str, mutate: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> dict[str, Any]:
        from google.cloud import firestore

        reference = self._cases.document(case_id)

        @firestore.transactional
        def _apply(transaction: Any) -> dict[str, Any]:
            # The read must be inside the transaction, or the write has nothing to conflict with
            # and the whole thing is an expensive `set()`. Firestore retries `_apply` when the
            # document changed between this read and the commit, so `mutate` gets re-run against
            # the newer document -- which is why it must be a pure function of what it is handed.
            snapshot = reference.get(transaction=transaction)
            if not snapshot.exists:
                raise LookupError(case_id)
            updated = mutate(dict(snapshot.to_dict() or {}))
            transaction.set(reference, updated)
            return updated

        return _apply(self._client.transaction())

    def cases_for(self, subject_id: str, as_of: str) -> list[dict[str, Any]]:
        # One equality filter, then filter the rest here. Two equality filters need a composite
        # index, which would have to be provisioned before a read could succeed -- a deploy-time
        # dependency for a query returning a handful of documents. Firestore indexes every single
        # field automatically, so one filter always works.
        query = self._cases.where(filter=FieldFilter("as_of", "==", as_of))
        return [
            document
            for document in (doc.to_dict() for doc in query.stream())
            if document.get("subject_id") == subject_id
        ]

    def cases_by_recurrence(self, recurrence_key: str) -> list[dict[str, Any]]:
        # One equality filter and no ordering, for the reason recorded above: every additional
        # constrained field needs a composite index provisioned before the read can succeed. The
        # caller filters by date in Python, which for a handful of cases per fund is the right trade.
        query = self._cases.where(filter=FieldFilter("recurrence_key", "==", recurrence_key))
        return [doc.to_dict() for doc in query.stream()]

    def record_stage(self, case_id: str, sequence: int, entry: dict[str, Any]) -> None:
        # Imported here, like `record_decision` does: a module-scope import would pull the Google
        # SDK into every offline run.
        from google.cloud.exceptions import Conflict

        doc = self._stage_docs.document(_stage_id(case_id, sequence))
        try:
            # `create()`, not `set()`: it fails when the document exists, which is what makes two
            # deliveries of the same event collide instead of one silently overwriting the other.
            doc.create({"case_id": case_id, "sequence": sequence, **entry})
        except Conflict as exc:
            # `Conflict`, not `type(exc).__name__ == "AlreadyExists"`. `AlreadyExists` is a
            # *subclass* of `Conflict`, so matching on the class name re-raised a plain `Conflict`
            # untranslated -- and `remediation_runner` catches only `ImmutableRecord`, so a
            # duplicate Pub/Sub delivery would raise instead of being the idempotent no-op the
            # at-least-once tests claim to guarantee. The subscription then retries forever and the
            # dead-letter topic fills with events that were in fact handled. `record_decision`
            # forty lines below already used `Conflict`, and that path was verified against the
            # live database.
            raise ImmutableRecord(
                f"stage {sequence} of {case_id} is already recorded. A case has one stage at "
                f"each position, so this delivery has already been handled."
            ) from exc

    def stages_for(self, case_id: str) -> list[dict[str, Any]]:
        query = self._stage_docs.where(filter=FieldFilter("case_id", "==", case_id))
        return sorted(
            (doc.to_dict() for doc in query.stream()), key=lambda e: e.get("sequence", 0)
        )

    def record_observation(self, observation: Observation) -> None:
        doc = self._observation_docs.document(observation.observation_id)
        snapshot = doc.get()
        if snapshot.exists:
            # Content-derived ids mean a repeat is the same observation. Only a *different* body
            # under the same id is a real conflict, and that means the derivation changed.
            if snapshot.to_dict() != observation.model_dump(mode="json"):
                raise ImmutableRecord(
                    f"observation {observation.observation_id} already exists with different "
                    f"content"
                )
            return
        doc.set(observation.model_dump(mode="json"))

    def observations_for(self, case_id: str) -> list[Observation]:
        query = self._observation_docs.where(filter=FieldFilter("case_id", "==", case_id))
        return [Observation.model_validate(doc.to_dict()) for doc in query.stream()]

    def record_decision(
        self, case_id: str, trace_id: str | None, sequence: int, decision: PolicyDecision
    ) -> None:
        doc = self._decision_docs.document(_decision_id(case_id, trace_id, sequence))
        # `create` rather than `set`: it fails if the document exists, which is exactly the
        # append-only rule, enforced by the store rather than by a read-then-write that two
        # instances could interleave.
        from google.cloud.exceptions import Conflict

        try:
            doc.create(
                {
                    "case_id": case_id,
                    "trace_id": trace_id,
                    "sequence": sequence,
                    **decision.as_span_attributes(),
                }
            )
        except Conflict as exc:
            raise ImmutableRecord(
                f"a policy decision already exists at {doc.id}. The governance log is append-only."
            ) from exc

    def decisions_for(self, case_id: str) -> list[dict[str, Any]]:
        # Equality filter only, ordered here. `.where(...).order_by(...)` needs a composite index
        # and fails with FailedPrecondition until one exists -- measured against the live database.
        # A case carries a handful of decisions, so ordering them client-side costs nothing and
        # removes an index the deployment would otherwise have to provision before any read worked.
        query = self._decision_docs.where(filter=FieldFilter("case_id", "==", case_id))
        return sorted((doc.to_dict() for doc in query.stream()), key=_decision_order)


def build(backend: str) -> Repository:
    """`memory` or `firestore`. Unknown backends are refused rather than defaulted.

    Defaulting an unrecognised name to memory is how a deployment ends up writing its audit trail
    to a dict that vanishes when the instance scales down.
    """
    if backend == "memory":
        return InMemoryRepository()
    if backend == "firestore":
        return FirestoreRepository()
    raise ValueError(
        f"unknown repository backend {backend!r}. Use 'memory' or 'firestore' -- an unrecognised "
        f"name is not defaulted, because the default would be the one that loses the audit trail."
    )
