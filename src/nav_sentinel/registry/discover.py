"""Capability-based agent discovery.

Triage does not hold a hard-coded map from break category to investigator. It asks the
registry which agents declare that they handle the category, and dispatches to the highest
version that does. Adding a specialist is therefore a registry publish, not a code change,
and removing one degrades gracefully to an explicit "no authorised investigator" outcome
rather than a silent misroute.
"""

from __future__ import annotations

from functools import lru_cache

from nav_sentinel.domain.models import BreakCategory
from nav_sentinel.registry.models import AgentManifest, load_manifests


def _version_key(version: str) -> tuple[int, ...]:
    parts = []
    for chunk in version.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


@lru_cache(maxsize=1)
def _catalogue() -> tuple[AgentManifest, ...]:
    return tuple(load_manifests())


def all_agents() -> list[AgentManifest]:
    return list(_catalogue())


def discover_for_category(category: BreakCategory) -> AgentManifest | None:
    """Highest-versioned agent declaring support for `category`, or None."""
    candidates = [m for m in _catalogue() if category in m.handles_categories]
    if not candidates:
        return None
    return max(candidates, key=lambda m: _version_key(m.version))


def get(agent_id: str) -> AgentManifest:
    for m in _catalogue():
        if m.agent_id == agent_id:
            return m
    raise KeyError(f"agent {agent_id!r} is not published in the registry")


def coverage() -> dict[str, str | None]:
    """Which categories currently have an authorised investigator. Surfaced on the
    exception console so a gap in fleet coverage is visible rather than latent."""
    out: dict[str, str | None] = {}
    for cat in BreakCategory:
        if cat == BreakCategory.UNCLASSIFIED:
            continue
        m = discover_for_category(cat)
        out[cat.value] = m.ref if m else None
    return out
