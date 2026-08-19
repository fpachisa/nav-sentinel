"""Composition root: the one place that knows both the control plane and every process.

It lives outside `control_plane/` and `registry/` on purpose. Something has to import both sides
to wire them together, and if that something sat inside the control plane then the control plane
would import a process package and the whole seam would be decorative.

Every entry point calls `configure()` first: the CLI, the compliance probe, the server, and the
test session fixture.
"""

from __future__ import annotations

from nav_sentinel.control_plane import packs
from nav_sentinel.registry import discover

# Manifests are sourced from the packs, so any registration change must drop the discovery
# cache or a newly registered process's agents stay invisible. Wired once, here, rather than
# left as an obligation on every caller.
packs.on_change(discover.invalidate)


def configure() -> tuple[packs.ProcessPack, ...]:
    """Register every process this deployment hosts.

    Adding a process is one import and one line here. Nothing in `control_plane/` or `registry/`
    changes, which is the claim the architecture rests on and the one a reader can check with
    `git diff --stat`.
    """
    from nav_sentinel.domain.pack import PACK as NAV_PACK

    # Registry discovery is a platform capability rather than a fund-accounting one, so it is
    # registered here instead of inside a pack. Two processes both needing it would otherwise
    # collide on the tool name and the second to register would fail.
    packs.register_platform_tools(
        packs.ToolSpec(
            "registry.discover_for_capability", discover.discover_for_capability, ("registry",),
            description="Highest-versioned agent declaring support for a capability.",
        ),
        packs.ToolSpec(
            "registry.coverage", discover.coverage, ("registry",),
            description="Which capabilities currently have an authorised investigator.",
        ),
    )
    packs.register(NAV_PACK)
    return packs.registered()


def reset() -> None:
    """Tear down all registration. Tests only."""
    packs.clear()
