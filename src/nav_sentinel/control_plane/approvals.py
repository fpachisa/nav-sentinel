"""Approval records: the only thing that lets an entry reach the ledger.

P-003 accepted `human_approval_ref` as an unvalidated string, so an agent could invent one.
`authorize_posting(..., human_approval_ref="APPR-whatever")` returned ALLOW on a reference that
resolved to nothing. A control that accepts its own evidence from the party it is controlling is
not a control.

Records are held in Firestore in a deployment and in memory when it is unavailable, behind one
interface. The in-memory store is not a convenience: it keeps the offline suite hermetic, and it
is the same code path, so a test proving a forged reference is refused proves it for both.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from nav_sentinel.config import settings
from nav_sentinel.control_plane.governance import ApprovalClass


class ApprovalRecord(BaseModel):
    """A human's signature on one case.

    Bound to the case *and* the band it was granted under. An approval given when the case
    scored single-reviewer must not survive the case being re-scored as CIO escalation — which is
    what a bare reference string would have allowed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: str
    case_id: str
    granted_band: ApprovalClass
    approvers: tuple[str, ...]
    granted_at: datetime
    note: str = ""

    def satisfies(self, band: ApprovalClass) -> tuple[bool, str]:
        """Whether this record authorises action at `band`."""
        if self.granted_band is not band:
            return False, (
                f"approval {self.ref} was granted at {self.granted_band.value} but the case "
                f"now bands to {band.value}"
            )
        required = 2 if band is ApprovalClass.FOUR_EYES else 1
        if len(set(self.approvers)) < required:
            return False, (
                f"approval {self.ref} carries {len(set(self.approvers))} approver(s); "
                f"{band.value} requires {required}"
            )
        return True, f"approval {self.ref} by {', '.join(self.approvers)}"


class ApprovalStore(ABC):
    @abstractmethod
    def get(self, ref: str) -> ApprovalRecord | None: ...

    @abstractmethod
    def put(self, record: ApprovalRecord) -> None: ...


class InMemoryApprovalStore(ApprovalStore):
    def __init__(self) -> None:
        self._records: dict[str, ApprovalRecord] = {}

    def get(self, ref: str) -> ApprovalRecord | None:
        return self._records.get(ref)

    def put(self, record: ApprovalRecord) -> None:
        if record.ref in self._records:
            raise ValueError(f"approval {record.ref} already exists and records are immutable")
        self._records[record.ref] = record

    def clear(self) -> None:
        self._records.clear()


class FirestoreApprovalStore(ApprovalStore):
    """Append-only approvals collection.

    Pulled forward from S2a because P-003 cannot be closed without it: validating a reference
    requires somewhere to validate it against.
    """

    def __init__(self) -> None:
        from google.cloud import firestore

        s = settings()
        self._client = firestore.Client(project=s.project)
        self._collection = self._client.collection(f"{s.firestore_collection_prefix}_approvals")

    def get(self, ref: str) -> ApprovalRecord | None:
        doc = self._collection.document(ref).get()
        return ApprovalRecord.model_validate(doc.to_dict()) if doc.exists else None

    def put(self, record: ApprovalRecord) -> None:
        doc = self._collection.document(record.ref)
        if doc.get().exists:
            raise ValueError(f"approval {record.ref} already exists and records are immutable")
        doc.set(record.model_dump(mode="json"))


_store: ApprovalStore = InMemoryApprovalStore()


def use_store(store: ApprovalStore) -> None:
    global _store
    _store = store


def store() -> ApprovalStore:
    return _store


def resolve(ref: str | None) -> ApprovalRecord | None:
    return None if ref is None else _store.get(ref)


def grant(
    case_id: str, band: ApprovalClass, approvers: tuple[str, ...], note: str = ""
) -> ApprovalRecord:
    """Record a human approval. Called by the approval console, never by an agent.

    The reference is derived from the content rather than issued as a counter, so it cannot be
    guessed from a sequence and two identical grants collide rather than silently duplicating.
    """
    granted_at = datetime.now(UTC)
    digest = hashlib.sha256(
        f"{case_id}|{band.value}|{'|'.join(sorted(approvers))}|{granted_at.isoformat()}".encode()
    ).hexdigest()[:16]
    record = ApprovalRecord(
        ref=f"APPR-{digest}",
        case_id=case_id,
        granted_band=band,
        approvers=approvers,
        granted_at=granted_at,
        note=note,
    )
    _store.put(record)
    return record
