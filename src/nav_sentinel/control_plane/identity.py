"""Agent identity.

Each agent runs under its own service account, minted from its registry manifest, and holds only
the data scopes that manifest declares. There is no shared fleet identity and no ambient
authority: an investigator that is compromised or simply wrong can reach exactly the read-only
surface it was published with, and nothing else.

Identities are bound by *reference* and resolved from the published registry. Binding a manifest
object meant the caller supplied the document describing its own authority -- an agent could bind
a copy of its own manifest with `may_post_entries` set true and every downstream check believed
it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from nav_sentinel.config import settings
from nav_sentinel.registry.models import AgentManifest

_current: ContextVar[AgentManifest | None] = ContextVar("current_agent", default=None)


class IdentityError(RuntimeError):
    pass


@contextmanager
def acting_as(agent_ref: str) -> Iterator[AgentManifest]:
    """Bind the calling context to one published agent identity.

    Takes an agent *reference* -- an id, or `id@version` -- and resolves the manifest from the
    published registry. It used to take a manifest object, which meant the caller supplied the
    very document that described its own authority: an agent could bind a copy of its own
    manifest with `may_post_entries` set true, and every `authorize_*` downstream believed it.

    Resolving here means a forged manifest cannot enter the context at all. There is no argument
    through which one can be supplied.
    """
    from nav_sentinel.registry import discover

    # Refuse a manifest object explicitly. Passing one raises AttributeError on the split below,
    # which is a refusal by accident: the caller gets an obscure error instead of being told that
    # supplying a manifest is the thing that is not allowed.
    if not isinstance(agent_ref, str):
        raise IdentityError(
            f"acting_as takes an agent reference, not a {type(agent_ref).__name__}. Identities "
            f"resolve from the published registry; a manifest supplied by the caller is exactly "
            f"what this signature exists to prevent."
        )

    agent_id = agent_ref.split("@", 1)[0]
    try:
        manifest = discover.get(agent_id)
    except KeyError as exc:
        raise IdentityError(
            f"{agent_ref!r} is not published in the registry. An identity must resolve to a "
            f"published manifest; it cannot be constructed at call time."
        ) from exc

    if "@" in agent_ref and manifest.ref != agent_ref:
        raise IdentityError(
            f"{agent_ref!r} is pinned to a version the registry does not publish "
            f"(current: {manifest.ref}). Refusing to silently bind a different version."
        )

    token = _current.set(manifest)
    try:
        yield manifest
    finally:
        _current.reset(token)


def current() -> AgentManifest:
    m = _current.get()
    if m is None:
        raise IdentityError(
            "No agent identity bound. Tool calls must run inside `identity.acting_as(agent_ref)` "
            "so that the gateway can attribute and authorise them."
        )
    return m


def current_or_none() -> AgentManifest | None:
    return _current.get()


def service_account_email(manifest: AgentManifest) -> str:
    return f"{manifest.service_account_id}@{settings().project}.iam.gserviceaccount.com"
