"""Composition root: the one place that knows both the control plane and every process.

It lives outside `control_plane/` and `registry/` on purpose. Something has to import both sides
to wire them together, and if that something sat inside the control plane then the control plane
would import a process package and the whole seam would be decorative.

Every entry point calls `configure()` first: the CLI, the compliance probe, the server, and the
test session fixture.
"""

from __future__ import annotations

import os

from nav_sentinel.control_plane import approvals, gateway, packs, repository
from nav_sentinel.registry import discover

# Manifests are sourced from the packs, so any registration change must drop the discovery
# cache or a newly registered process's agents stay invisible. Wired once, here, rather than
# left as an obligation on every caller.
packs.on_change(discover.invalidate)


#: The repository the fleet persists to. Set by `configure`, read through `store()`, so nothing
#: holds a reference across a reconfiguration.
_repository: repository.Repository | None = None
#: Which approvals backend is installed, so a later `configure()` with no arguments does not
#: silently replace it.
_approvals_backend: str | None = None


def _installed_repository_backend() -> str | None:
    if _repository is None:
        return None
    return "firestore" if type(_repository).__name__ == "FirestoreRepository" else "memory"


def configure(
    *, approvals_backend: str | None = None, repository_backend: str | None = None
) -> tuple[packs.ProcessPack, ...]:
    """Register every process this deployment hosts.

    Adding a process is one import and one line here. Nothing in `control_plane/` or `registry/`
    changes, which is the claim the architecture rests on and the one a reader can check with
    `git diff --stat`.
    """
    global _repository, _approvals_backend

    # Omitting a backend means "leave whatever is installed", not "use memory". `configure()` is
    # called by every entry point *and* at the top of `cycle_runner.run`, so a defaulting second
    # call silently downgraded both backends: measured, a service configured for Firestore at
    # startup got an InMemoryRepository on its first cycle, and wrote its audit trail to a dict
    # that vanishes when the instance scales down. Passing a backend explicitly always applies it.
    # Precedence, in order: an explicit repository backend; else the explicit approvals backend,
    # since the two answer the same question and a service persisting approvals durably while
    # writing its governance log to memory would be the worst of both; else whatever is already
    # installed; else memory.
    # `NAV_REPOSITORY` sits between the explicit argument and whatever is installed, so a CLI can
    # be pointed at the durable store without every entry point growing a flag -- and so the
    # cross-process claim can actually be run: `NAV_REPOSITORY=firestore make demo` then
    # `NAV_REPOSITORY=firestore make approve` is two processes sharing one trail.
    from_env = os.environ.get("NAV_REPOSITORY")
    requested = (
        repository_backend
        or approvals_backend
        or from_env
        or _installed_repository_backend()
        or "memory"
    )
    approvals_backend = approvals_backend or _approvals_backend or from_env or "memory"

    if requested != _installed_repository_backend():
        _repository = repository.build(requested)
    _approvals_backend = approvals_backend

    from nav_sentinel.domain.pack import PACK as NAV_PACK
    from nav_sentinel.memory import recurrence
    from nav_sentinel.remediation_office.pack import PACK as REM_PACK
    from nav_sentinel.transfer_agency.pack import PACK as TA_PACK

    # Registry discovery is a platform capability rather than a fund-accounting one, so it is
    # registered here instead of inside a pack. Two processes both needing it would otherwise
    # collide on the tool name and the second to register would fail.
    packs.register_platform_tools(
        packs.ToolSpec(
            "registry.discover_for_capability", discover.discover_for_capability, ("registry",),
            source="agent_registry", uri_template="registry://agents/{capability}",
            description="Highest-versioned agent declaring support for a capability.",
        ),
        packs.ToolSpec(
            "registry.coverage", discover.coverage, ("registry",),
            source="agent_registry", uri_template="registry://coverage",
            description="Which capabilities currently have an authorised investigator.",
        ),
        # Recurrence. A platform tool rather than a pack's, because "what happened to this subject
        # before" is not one process's question -- and because `memory` is process-side in the seam
        # scan, so no pack may import it and only this root may introduce the two.
        #
        # The store is bound here rather than passed by the agent: an agent that could name the
        # store it reads could name one that says what suits it.
        packs.ToolSpec(
            "memory.prior_errors",
            lambda fund_id, since, excluding="": recurrence.prior_errors(
                store(), fund_id, since, excluding=excluding
            ),
            ("case_history",),
            observe=recurrence.observe,
            facts=("prior_errors", "since", "fund_id"),
            source="recorded_case_history",
            uri_template="memory://recurrence/{fund_id}",
            description="How many errors this fund has recorded on or after a date, with the case "
                        "ids behind the count. A recurring failure is not assessed like an "
                        "isolated one, so the count changes the threshold that applies.",
        ),
    )
    packs.register(NAV_PACK)
    # A second process, and this is the whole change. Its capabilities, tools, manifests, prompts
    # and thresholds all come from the pack; the control plane and the registry are untouched, which
    # `git diff --stat` shows rather than asserts.
    packs.register(TA_PACK)
    # The third process. It coordinates the other two and reaches them only through the delegation
    # its pack declares -- so registering it changes nothing about either.
    packs.register(REM_PACK)

    # How the gateway runs an agent. Injected here because the control plane may not import the
    # agents layer: `agents` is process-side and the seam test forbids any path from the platform to
    # it, including under TYPE_CHECKING. The gateway declares what it needs, the composition root
    # decides what satisfies it, and an offline test supplies a fake.
    gateway.register_agent_invoker(_invoke_agent)
    _configure_approvals(approvals_backend)
    return packs.registered()


def _invoke_agent(manifest, brief, **kwargs):
    """Run one agent on one brief. The gateway's injected invoker.

    Synchronous by signature and asynchronous underneath, because `gateway.delegate` is called from
    synchronous code -- a stage handler -- while `investigator.investigate` is a coroutine. Bridging
    here rather than making the gateway async keeps the platform free of an event-loop assumption it
    has never needed.
    """
    import asyncio

    from nav_sentinel.agents import investigator

    return asyncio.run(investigator.investigate(brief, manifest, **kwargs))


def _configure_approvals(backend: str) -> None:
    """Install the store the enforcement side reads approvals from.

    `memory` is the default because the offline suite must be hermetic — but it is now an explicit
    choice rather than a silent one. Leaving an in-process dict as the shipped default meant a
    deployment would have given every Cloud Run instance its own empty store: an approval granted
    on one instance invisible to the next, and every approval lost on cold start.

    `firestore` fails closed. A store that cannot be reached must stop the fleet, not degrade to
    one the controlled party can write.
    """
    if backend == "memory":
        approvals.use_store(approvals.InMemoryApprovalStore())
        return
    if backend != "firestore":
        raise ValueError(f"unknown approvals backend {backend!r}; expected 'memory' or 'firestore'")

    try:
        approvals.use_store(approvals.FirestoreApprovalStore())
    except Exception as exc:
        raise RuntimeError(
            "the Firestore approvals store is unavailable. Refusing to fall back to an "
            "in-process store: nothing may post to the ledger on the strength of an approval "
            "record the agent runtime could have written itself."
        ) from exc


def store() -> repository.Repository:
    """The configured repository.

    Raises rather than building one on demand: a lazily created store would be an in-memory one in
    a deployment that meant to use Firestore, and the failure would be a silently empty audit trail
    rather than a startup error.
    """
    if _repository is None:
        raise RuntimeError(
            "no repository is configured. Call nav_sentinel.composition.configure() at your entry "
            "point; the audit trail has nowhere to go until you do."
        )
    return _repository


def approval_authority() -> approvals.ApprovalAuthority:
    """The minting side, for the approval console.

    Deliberately a separate call. The agent runtime never invokes it, so the process that acts on
    approvals holds no object capable of creating one.
    """
    return approvals.ApprovalAuthority(approvals._writable())


def reset() -> None:
    """Tear down all registration. Tests only."""
    global _repository, _approvals_backend

    _repository = None
    _approvals_backend = None
    packs.clear()
