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

#: A binding is the manifest plus a token only `acting_as` holds. `current()` checks the token,
#: so writing the ContextVar directly -- `identity._current.set(forged)` -- yields an unbound
#: identity rather than a forged one. This does not defend against arbitrary code execution inside
#: the runtime, which could read the token; it closes the route that needs nothing but a name.
_BINDING_TOKEN = object()

_current: ContextVar[tuple[AgentManifest, object] | None] = ContextVar(
    "current_agent", default=None
)


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
    # `type(...) is str`, not isinstance: a str subclass overriding split() could bind a
    # different published agent than the reference names, which is an attribution hazard
    # the moment a reference arrives from outside the process.
    if type(agent_ref) is not str:
        raise IdentityError(
            f"acting_as takes an agent reference, not a {type(agent_ref).__name__}. Identities "
            f"resolve from the published registry; a manifest supplied by the caller is exactly "
            f"what this signature exists to prevent."
        )

    try:
        manifest = discover.get_ref(agent_ref)
    except KeyError as exc:
        raise IdentityError(
            f"{exc.args[0]} An identity must resolve to a published manifest; it cannot be "
            f"constructed at call time."
        ) from exc

    token = _current.set((manifest, _BINDING_TOKEN))
    try:
        yield manifest
    finally:
        _current.reset(token)


def current() -> AgentManifest:
    binding = _current.get()
    if binding is not None:
        # Shape-checked before unpacking: writing a bare manifest into the ContextVar otherwise
        # raised "too many values to unpack", which is a refusal by accident rather than a
        # statement of what went wrong.
        if (
            isinstance(binding, tuple)
            and len(binding) == 2
            and binding[1] is _BINDING_TOKEN
        ):
            return binding[0]
        raise IdentityError(
            "An identity was bound without going through acting_as. Bindings carry a token that "
            "only acting_as issues, so this is either a bug or an attempt to install an "
            "unresolved manifest."
        )
    raise IdentityError(
        "No agent identity bound. Tool calls must run inside `identity.acting_as(agent_ref)` so "
        "that the gateway can attribute and authorise them."
    )


def current_or_none() -> AgentManifest | None:
    binding = _current.get()
    return binding[0] if binding is not None and binding[1] is _BINDING_TOKEN else None


def service_account_email(manifest: AgentManifest) -> str:
    return f"{manifest.service_account_id}@{settings().project}.iam.gserviceaccount.com"
