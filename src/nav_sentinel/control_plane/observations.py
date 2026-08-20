"""What a tool call did, recorded so a citation can be checked against it.

The platform's half of the evidence mechanism. An `Observation` is one call: which agent, on whose
behalf, on which case, what came back, and a digest of it. Verdict citations resolve against these,
so an investigator that cites a rate it never fetched has nothing to cite with.

**`observed` is a plain string mapping, and that is deliberate.** The facts worth citing are in each
process's own vocabulary -- `rate` and `rate_date` for NAV reconciliation, share counts for transfer
agency -- and the control plane cannot be allowed to know either. So a process supplies the
projection (`ToolSpec.observe`) and the platform stores the result opaquely, exactly as `CaseFacts`
reduces three domain enums to plain strings on the way in. The control plane cannot reach back
through it, which is what lets a second process be hosted without touching this module.

The values are strings rather than numbers for the same reason: a `Decimal` here would be a domain
type in a platform record, and the audit trail wants the text anyway.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterator, Mapping


class Observation(BaseModel):
    """One tool call, as it happened.

    Frozen: an audit record that can be edited after the fact is not one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_id: str
    case_id: str
    trace_id: str | None = None
    agent_ref: str
    tool: str
    args: str                      # canonicalised, for the audit record
    digest: str                    # over the returned value
    retrieved_at: datetime
    source: str
    source_uri: str | None = None
    #: Process-supplied facts, stringified. Opaque to the control plane by design.
    observed: dict[str, str] = Field(default_factory=dict)
    trusted: bool = True
    armor_verdict: str | None = None
    summary: str = ""

    @property
    def namespace(self) -> str:
        return self.tool.partition(".")[0]


class ObservationStore:
    """The observations recorded while investigating one case.

    Keyed by `observation_id`, which is content-derived, so the same call recorded twice is one
    observation rather than two. Held behind this interface so S2a can back it with Firestore
    without an investigator changing.

    A plain dict per case, not a ContextVar. The decision log needed one because a *process-global*
    list was being cleared per request; an object created per case and handed to its own tool
    surface has neither problem, and cannot leak into another case.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, Observation] = {}

    def record(self, observation: Observation) -> Observation:
        self._by_id[observation.observation_id] = observation
        return observation

    def get(self, observation_id_: str) -> Observation | None:
        return self._by_id.get(observation_id_)

    def __contains__(self, observation_id_: object) -> bool:
        return observation_id_ in self._by_id

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[str]:
        return iter(self._by_id)

    def as_mapping(self) -> dict[str, Observation]:
        """A snapshot for callers that only read."""
        return dict(self._by_id)

    def tools_used(self) -> frozenset[str]:
        return frozenset(o.tool for o in self._by_id.values())

    def namespaces(self) -> frozenset[str]:
        """Which tool namespaces this case has evidence from -- what an evidence requirement reads."""
        return frozenset(o.namespace for o in self._by_id.values())


def canonical(value: object) -> str:
    """A stable text form for a returned value.

    `Decimal` and `date` are rendered as their own text rather than repr'd, so a digest does not
    move with a library version -- S8a requires a byte-identical re-run.
    """
    if isinstance(value, Decimal | date | datetime):
        return str(value)
    if isinstance(value, dict):
        return "{" + ",".join(f"{k}:{canonical(value[k])}" for k in sorted(map(str, value))) + "}"
    if isinstance(value, list | tuple):
        return "[" + ",".join(canonical(v) for v in value) + "]"
    if hasattr(value, "model_dump"):
        return canonical(value.model_dump())
    return repr(value)


def digest_of(value: object) -> str:
    """A digest over a returned value. For the audit record, deliberately not a lookup key.

    A digest handed to a model can be quoted without the data behind it having been read, which is
    why citations resolve by `observation_id` instead.
    """
    return hashlib.sha256(canonical(value).encode()).hexdigest()[:16]


def observation_id(case_id: str, tool: str, args: str, digest: str) -> str:
    """A stable id for one call, derived rather than counted.

    Content-derived so a re-run produces the same ids; `itertools.count` was called out in this
    project for exactly this reason.
    """
    material = "|".join((case_id, tool, args, digest))
    return f"OBS-{hashlib.sha256(material.encode()).hexdigest()[:16]}"


def stringify(projected: Mapping[str, object]) -> dict[str, str]:
    """Reduce a process's projected facts to text, dropping what it did not observe.

    The parameter is `projected`, not `facts`: inside the control plane `facts` names a `CaseFacts`,
    and the seam test scans attribute reads on that name to catch the platform reaching into a
    domain object. A plain mapping borrowing the name would have tripped a check that is worth
    keeping strict.
    """
    return {k: canonical(v) for k, v in projected.items() if v is not None}


def utcnow() -> datetime:
    """One place, so a recorded time is always timezone-aware."""
    return datetime.now(UTC)
