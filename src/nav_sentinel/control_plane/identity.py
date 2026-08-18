"""Agent identity.

Each agent runs under its own service account, minted from its registry manifest, and
holds only the data scopes that manifest declares. There is no shared fleet identity and no
ambient authority: an investigator that is compromised or simply wrong can reach exactly the
read-only surface it was published with, and nothing else.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from nav_sentinel.config import settings
from nav_sentinel.registry.models import AgentManifest

_current: ContextVar[AgentManifest | None] = ContextVar("current_agent", default=None)


class IdentityError(RuntimeError):
    pass


@contextmanager
def acting_as(manifest: AgentManifest) -> Iterator[AgentManifest]:
    """Bind the calling context to one agent identity. The gateway reads this, so a tool
    call made outside any identity is refused rather than defaulted."""
    token = _current.set(manifest)
    try:
        yield manifest
    finally:
        _current.reset(token)


def current() -> AgentManifest:
    m = _current.get()
    if m is None:
        raise IdentityError(
            "No agent identity bound. Tool calls must run inside `identity.acting_as(manifest)` "
            "so that the gateway can attribute and authorise them."
        )
    return m


def current_or_none() -> AgentManifest | None:
    return _current.get()


def service_account_email(manifest: AgentManifest) -> str:
    return f"{manifest.service_account_id}@{settings().project}.iam.gserviceaccount.com"
