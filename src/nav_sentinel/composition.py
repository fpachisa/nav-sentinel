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


def configure() -> tuple[packs.ProcessPack, ...]:
    """Register every process this deployment hosts.

    Adding a process is one import and one line here. Nothing in `control_plane/` or `registry/`
    changes, which is the claim the architecture rests on and the one a reader can check with
    `git diff --stat`.
    """
    from nav_sentinel.domain.pack import PACK as NAV_PACK

    packs.register(NAV_PACK)

    # Manifests are sourced from the packs, so a newly registered process must invalidate the
    # discovery cache or its agents stay invisible.
    discover.invalidate()
    return packs.registered()


def reset() -> None:
    """Tear down all registration. Tests only."""
    packs.clear()
    discover.invalidate()
