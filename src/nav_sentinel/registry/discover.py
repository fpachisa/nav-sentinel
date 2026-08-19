"""Capability-based agent discovery.

Triage does not hold a hard-coded map from break category to investigator. It asks the registry
which agents declare a capability, and dispatches to the highest version that does. Adding a
specialist is therefore a registry publish, not a code change, and removing one degrades to an
explicit "no authorised investigator" outcome rather than a silent misroute.

Capabilities are namespaced strings rather than a closed enum. An enum belonging to one domain
could never route a second process, and it made this module import the fund-accounting models.
"""

from __future__ import annotations

from nav_sentinel.control_plane import packs
from nav_sentinel.registry.models import AgentManifest, load_manifests

_cache: tuple[AgentManifest, ...] | None = None


def _version_key(version: str) -> tuple[int, ...]:
    parts = []
    for chunk in version.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _catalogue() -> tuple[AgentManifest, ...]:
    """Published manifests, cached but invalidatable.

    An `lru_cache` here meant a long-running service never saw a republished manifest, which
    quietly falsified the claim that adding a specialist is a publish rather than a code change.
    """
    global _cache
    if _cache is None:
        _cache = tuple(load_manifests())
    return _cache


def invalidate() -> None:
    """Drop the cached catalogue. Called after a publish, and by tests."""
    global _cache
    _cache = None


def all_agents() -> list[AgentManifest]:
    return list(_catalogue())


def discover_for_capability(capability: str) -> AgentManifest | None:
    """Highest-versioned agent declaring `capability`, or None."""
    candidates = [m for m in _catalogue() if capability in m.handles_capabilities]
    if not candidates:
        return None
    return max(candidates, key=lambda m: _version_key(m.version))


def get(agent_id: str) -> AgentManifest:
    for m in _catalogue():
        if m.agent_id == agent_id:
            return m
    raise KeyError(f"agent {agent_id!r} is not published in the registry")


def coverage() -> dict[str, str | None]:
    """Which capabilities currently have an authorised investigator.

    The universe of capabilities comes from the **registered process packs**, not from the
    published manifests. Taking it from manifests would make an uncovered capability vanish from
    coverage instead of reporting None — and refusing to route an unsupported capability is a
    governance outcome worth showing, not a gap to hide.
    """
    out: dict[str, str | None] = {}
    for capability in packs.capabilities():
        m = discover_for_capability(capability)
        out[capability] = m.ref if m else None
    return out
