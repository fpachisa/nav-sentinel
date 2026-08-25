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
        candidate = tuple(load_manifests())
        # Validated on first load too, not only on republish. The invariants were enforced on the
        # harder path and not the easier one: dropping a YAML claiming `may_post_entries: true` into
        # the manifest directory was adopted unvalidated, won routing on a higher version, and
        # `authorize_posting` returned ALLOW. `republish()` refused the identical file. This
        # module's own argument -- an invariant only tests guard is not guarded -- applies to load.
        validate_fleet(candidate)
        _cache = candidate
    return _cache


class PublicationRefused(RuntimeError):
    """A manifest on disk violates a fleet invariant, so the registry will not adopt it."""


def validate_fleet(manifests: tuple[AgentManifest, ...]) -> None:
    """Re-assert what must be true of every published agent, whoever published it.

    These invariants lived only in tests, which meant they were asserted against the manifests
    *committed to the repository* and nowhere else. `acting_as` resolves from this catalogue, so a
    manifest adopted at runtime changes what every `authorize_*` believes -- and a test reading YAML
    files off disk cannot see that. Freezing the registry models closed mutation; it did nothing
    about publication.

    So the same checks run here, on the way in. `republish()` is a runtime authority-mutation
    surface, and one that only tests guard is not guarded.
    """
    for manifest in manifests:
        _validate_manifest(manifest)
    _validate_no_collisions(manifests)


def _validate_manifest(manifest: AgentManifest) -> None:
    """What must be true of one published agent."""
    catalogue = packs.catalogue()
    if manifest.authority.may_post_entries:
        raise PublicationRefused(
            f"{manifest.ref} claims posting authority. No agent may post: corrections are drafted "
            f"for a human to approve. P-003 would deny it at runtime, but a manifest asserting it "
            f"must not enter the registry at all."
        )
    if manifest.authority.max_autonomous_impact is not None:
        raise PublicationRefused(
            f"{manifest.ref} declares an autonomous ceiling of "
            f"{manifest.authority.max_autonomous_impact}. A manifest may narrow its own autonomy, "
            f"never widen it."
        )
    if manifest.authority.may_propose_remediation and manifest.agent_id != "remediation-agent":
        raise PublicationRefused(
            f"{manifest.ref} claims drafting authority. Only the remediation agent drafts; "
            f"investigators report causes."
        )
    owners = {
        owner.key
        for capability in manifest.handles_capabilities
        if (owner := packs.process_of(capability)) is not None
    }
    if len(owners) > 1:
        raise PublicationRefused(
            f"{manifest.ref} declares capabilities owned by more than one process "
            f"({sorted(owners)}). An agent belongs to one department. Two consequences otherwise, "
            f"both reached by a one-line YAML edit: the registry may route another process's "
            f"capability to this agent, and `packs.delegations_for` unions the delegations of every "
            f"pack owning any of its capabilities -- so a manifest could grant itself the right to "
            f"request what its own department may not. Delegation is declared on the pack precisely "
            f"so an agent's own document cannot widen it, and that was enforced by nothing."
        )
    if not manifest.allowed_tools:
        raise PublicationRefused(
            f"{manifest.ref} declares no tools and could never do any work."
        )

    phantom = [t for t in manifest.allowed_tools if t not in catalogue]
    if phantom:
        raise PublicationRefused(
            f"{manifest.ref} declares tool(s) {phantom} that no registered process provides. "
            f"A manifest naming a nonexistent tool is a deployment defect."
        )

    # A tool reading a domain the manifest does not scope is adopted, wins routing on a higher
    # version, then fails P-006 at runtime -- so a republish could silently disable an investigator.
    # The suite asserted this against the committed YAML only, which is no protection for a manifest
    # arriving at runtime.
    for name in manifest.allowed_tools:
        undeclared = [
            domain for domain in catalogue[name].reads if domain not in manifest.data_scopes.read
        ]
        if undeclared:
            raise PublicationRefused(
                f"{manifest.ref} is allowed {name!r}, which reads {undeclared}, but its "
                f"data_scopes.read does not declare them. P-006 would deny every call."
            )

    if manifest.untrusted_inputs and not manifest.requires_model_armor:
        raise PublicationRefused(
            f"{manifest.ref} declares untrusted_inputs without requires_model_armor. An agent that "
            f"reads the public internet cannot opt out of screening."
        )


def _validate_no_collisions(manifests: tuple[AgentManifest, ...]) -> None:
    """What must be true of the fleet taken together.

    Service-account ids are derived by truncating to Google's 30-character limit, so two long agent
    ids can land on one cloud identity -- and an agent silently sharing another's identity holds its
    IAM grants. `corporate-actions-investigator` is already truncated to
    `nav-corporate-actions-investig`, so refusing truncation outright would refuse the shipped
    fleet: the hazard is the collision, not the length. The suite asserted the *derived* id is at
    most 30 characters, which truncation guarantees, so that test could never have failed.
    """
    accounts: dict[str, str] = {}
    claimed: dict[str, str] = {}
    for manifest in manifests:
        account = manifest.service_account_id
        if accounts.setdefault(account, manifest.agent_id) != manifest.agent_id:
            raise PublicationRefused(
                f"{manifest.agent_id!r} and {accounts[account]!r} both derive the service account "
                f"{account!r}. One would hold the other's IAM grants."
            )
        for capability in manifest.handles_capabilities:
            if claimed.setdefault(capability, manifest.ref) != manifest.ref:
                raise PublicationRefused(
                    f"{manifest.ref} and {claimed[capability]} both claim {capability!r}. Routing "
                    f"is by highest version, so a republish could take over another agent's "
                    f"capability."
                )


def republish() -> tuple[AgentManifest, ...]:
    """Re-read the manifests from disk and adopt them, without a process restart.

    The registry cached for the lifetime of the process, and nothing triggered a reload:
    `packs.on_change` fires on pack registration, not on a manifest appearing. So "republishing a
    manifest changes routing without a restart" had no mechanism at all.

    Validation runs before adoption, and a refusal leaves the previous catalogue in place -- a
    half-adopted fleet is worse than a stale one. In-process republish is the demonstrated scope;
    a Firestore-backed publication path is later work, and this is the seam it would use.
    """
    global _cache
    candidate = tuple(load_manifests())
    validate_fleet(candidate)
    _cache = candidate
    return candidate


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
    """Highest published version of an agent id.

    Returned the *first* match in catalogue order, which is filename sort order, while
    `discover_for_capability` returned the highest version. With two versions of one id published
    the two functions disagreed, and the version-pin check in `identity.acting_as` reported a
    published version as unpublished.
    """
    candidates = [m for m in _catalogue() if m.agent_id == agent_id]
    if not candidates:
        raise KeyError(f"agent {agent_id!r} is not published in the registry")
    return max(candidates, key=lambda m: _version_key(m.version))


def get_ref(agent_ref: str) -> AgentManifest:
    """Resolve an exact `id@version`, or the highest version of a bare id.

    Separate from `get` because a pinned reference must match exactly: silently binding a
    different version would let a caller pin to one manifest's authority and receive another's.
    """
    if "@" not in agent_ref:
        return get(agent_ref)

    agent_id, version = agent_ref.split("@", 1)
    for m in _catalogue():
        if m.agent_id == agent_id and m.version == version:
            return m
    published = sorted(m.version for m in _catalogue() if m.agent_id == agent_id)
    raise KeyError(
        f"{agent_ref!r} is not published. Versions of {agent_id!r} in the registry: "
        f"{published or 'none'}"
    )


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
