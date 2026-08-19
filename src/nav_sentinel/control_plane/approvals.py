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
import re
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
    #: Roles the signers held, recorded so an auditor can see the signature was role-appropriate
    #: at the time and not merely counted.
    approver_roles: tuple[str, ...] = ()
    granted_at: datetime
    note: str = ""

    def satisfies(self, band: ApprovalClass) -> tuple[bool, str]:
        """Whether this record authorises action at `band`."""
        if self.granted_band is not band:
            return False, (
                f"approval {self.ref} was granted at {self.granted_band.value} but the case "
                f"now bands to {band.value}"
            )
        allowed_roles, required = BAND_REQUIREMENTS[band]
        if len(set(self.approvers)) < required:
            return False, (
                f"approval {self.ref} carries {len(set(self.approvers))} approver(s); "
                f"{band.value} requires {required}"
            )
        # Re-checked at enforcement, not only at minting. A record can reach the store without
        # going through `grant()` -- a direct write, a restored backup -- so checking roles once
        # made `approver_roles` decorative exactly where it mattered.
        wrong = sorted(set(self.approver_roles) - allowed_roles)
        if wrong:
            return False, (
                f"approval {self.ref} was signed by role(s) {wrong}; {band.value} may be signed "
                f"only by {sorted(allowed_roles)}"
            )
        if len(self.approver_roles) != len(self.approvers):
            return False, (
                f"approval {self.ref} records {len(self.approvers)} approver(s) and "
                f"{len(self.approver_roles)} role(s); a signature without a recorded role cannot "
                f"be checked"
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


class Principal(BaseModel):
    """An authenticated human, as the platform sees them.

    `approvers` was a tuple of arbitrary strings, so an agent could sign as `("i-am-the-cio",)`.
    Nothing linked an approval to a real person. A principal carries the subject the platform
    authenticated and the role it holds, and the band determines which roles suffice.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str
    role: str

    def __str__(self) -> str:
        return f"{self.subject} ({self.role})"


#: Which roles may sign at each band, and how many distinct principals are required.
BAND_REQUIREMENTS: dict[ApprovalClass, tuple[frozenset[str], int]] = {
    ApprovalClass.AUTO_CLEAR: (frozenset({"reviewer", "controller", "cio"}), 1),
    ApprovalClass.SINGLE_REVIEWER: (frozenset({"reviewer", "controller", "cio"}), 1),
    ApprovalClass.FOUR_EYES: (frozenset({"controller", "cio"}), 2),
    ApprovalClass.CIO_ESCALATION: (frozenset({"cio"}), 1),
}


class ApprovalDenied(RuntimeError):
    """A grant was refused. Raised at minting time, so a bad approval never exists."""


class ApprovalAuthority:
    """The only object that can mint an approval.

    Held by the approval console. **The agent runtime never constructs one**, which is the point:
    `grant()` used to be a module function, so anything that could import the module could sign
    its own approval. The module docstring's own standard — a control that accepts its own
    evidence from the party it is controlling is not a control — was not met by a module function.
    """

    def __init__(self, store: ApprovalStore) -> None:
        self._store = store

    def grant(
        self,
        case_id: str,
        band: ApprovalClass,
        principals: tuple[Principal, ...],
        note: str = "",
    ) -> ApprovalRecord:
        """Record a human approval, or refuse to.

        The reference is content-derived rather than sequential, so it cannot be guessed from a
        counter. It deliberately excludes the timestamp: including it meant two identical grants
        produced two distinct refs and both persisted, which is not what "records are immutable"
        should mean.
        """
        allowed_roles, required = BAND_REQUIREMENTS[band]
        if not principals:
            raise ApprovalDenied(
                f"an approval at {band.value} needs at least one signer; an append-only ledger "
                f"must not accept a signature with no signer"
            )
        wrong = sorted({p.role for p in principals} - allowed_roles)
        if wrong:
            raise ApprovalDenied(
                f"{band.value} may be signed by {sorted(allowed_roles)}; got role(s) {wrong}"
            )
        if len({p.subject for p in principals}) < required:
            raise ApprovalDenied(
                f"{band.value} requires {required} distinct signer(s); got "
                f"{len({p.subject for p in principals})}"
            )

        subjects = tuple(sorted(p.subject for p in principals))
        digest = hashlib.sha256(
            f"{case_id}|{band.value}|{'|'.join(subjects)}".encode()
        ).hexdigest()[:16]
        record = ApprovalRecord(
            ref=f"APPR-{digest}",
            case_id=case_id,
            granted_band=band,
            approvers=subjects,
            approver_roles=tuple(sorted(p.role for p in principals)),
            granted_at=datetime.now(UTC),
            note=note,
        )
        self._store.put(record)
        return record


_REF = re.compile(r"^APPR-[0-9a-f]{16}$")

_resolver: ApprovalStore = InMemoryApprovalStore()


def use_store(store: ApprovalStore) -> None:
    """Install the store the *enforcement* side reads from. Never returns a writer."""
    global _resolver
    _resolver = store


class _ReadOnlyStore(ApprovalStore):
    """A façade with no working `put`.

    `reader()` returned the store itself, `put()` included, while its docstring said it never
    returns a writer. Naming is not a control.
    """

    def __init__(self, inner: ApprovalStore) -> None:
        self._inner = inner

    def get(self, ref: str) -> ApprovalRecord | None:
        return self._inner.get(ref)

    def put(self, record: ApprovalRecord) -> None:  # noqa: ARG002 -- the signature is the point
        raise PermissionError(
            "this handle is read-only. Minting an approval requires an ApprovalAuthority, which "
            "the agent runtime never constructs."
        )


def reader() -> ApprovalStore:
    """A read-only handle on the installed store, for the enforcement side."""
    return _ReadOnlyStore(_resolver)


def _writable() -> ApprovalStore:
    """The writable store, for an ApprovalAuthority only."""
    return _resolver


def resolve(ref: str | None) -> ApprovalRecord | None:
    """Look up an approval. Read-only by construction.

    The reference shape is validated here rather than passed through. `FirestoreApprovalStore`
    raises `ValueError` on a reference like "a/b" (odd number of path elements), so a malformed
    reference produced an unhandled exception in a deployment instead of a recorded DENY — losing
    the governance-log entry for an escalation attempt, which is exactly what `call_tool` goes out
    of its way to avoid for an unknown tool.
    """
    if ref is None or not _REF.match(ref):
        return None
    return _resolver.get(ref)
