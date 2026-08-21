"""The process port: how a process registers itself with the control plane.

A process is a package of capabilities, tools, manifests and materiality thresholds. The
control plane knows this shape and nothing about any particular process, which is what lets a
second process be added without touching a line here.

`ToolSpec` lives in this module rather than beside the tools it describes. That inversion is the
point: the gateway previously imported `tools.catalogue` to resolve a name, which gave it a
transitive path to every fund-accounting model in the project. Now the control plane owns the
port and each process supplies specs through it.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from nav_sentinel.control_plane.governance import ThresholdSet


@dataclass(frozen=True)
class ToolSpec:
    name: str
    fn: Callable
    #: Data domains this tool reads, in the process's own vocabulary. P-006 compares these
    #: against the manifest's declared scopes as opaque strings.
    reads: tuple[str, ...] = ()
    #: True when the return value is authored outside our trust boundary. The gateway screens
    #: these; the agent is never asked to remember to.
    untrusted_output: bool = False
    #: Which fields of a structured return actually carry filer text. Empty means "everything",
    #: which is right for a tool returning a document body and wrong for one returning metadata:
    #: screening a listing's accession numbers and dates cost 15,000 calls to learn ten facts, and
    #: overflowed the span queue that carries the audit trail. An accession number is pattern-
    #: constrained by the SEC and cannot carry an instruction; an issuer name can.
    untrusted_fields: tuple[str, ...] = ()
    description: str = ""
    #: Projects this tool's return value onto the facts a verdict may cite, in the process's own
    #: vocabulary. The platform calls it, stringifies the result and stores it opaquely -- it never
    #: interprets the keys, the same way `CaseFacts` reduces domain enums to plain strings.
    #:
    #: Per tool rather than generic because a generic projection would have to guess which
    #: attribute of a holding is "the rate", and guessing wrong means a verdict cites a number that
    #: is not the one the tool returned. A tool without one contributes no facts, which is honest:
    #: it can still be cited as having been called.
    observe: Callable[[Any, Mapping[str, Any]], Mapping[str, object]] | None = None
    #: Which fact names `observe` may return. Declared, so a requirement can be validated against
    #: it at registration and a projection returning an undeclared key is caught rather than
    #: silently dropped -- `_observe_security` projected `domicile`, nothing consumed it, and the
    #: fact the corporate-action cross-check turns on was uncitable with no test failing.
    facts: tuple[str, ...] = ()
    #: Where evidence from this tool comes from, in the process's vocabulary -- "ecb_fx_reference
    #: _rates", "books_and_records". The platform stores it and never interprets it. Declared per
    #: tool rather than inferred from the namespace, which is what the platform used to do: a
    #: second process then got its bare namespace as a source name and `None` for every URI, so
    #: its evidence could not satisfy the criterion that every citation names a source.
    source: str = ""
    #: Derives the specific resource a result came from, when there is one. `edgar.search_filings`
    #: returns a per-filing URI; a books lookup has none, and saying so is better than inventing a
    #: constant that identifies nothing.
    locate: Callable[[Any], str | None] | None = None
    #: A stable URI for this tool's evidence when no per-result one exists -- a service endpoint, or
    #: a fixture path. Templated on the call's arguments so it identifies *this* retrieval rather
    #: than the service in general, which is what a constant per namespace gave and why two of three
    #: investigators could produce no citable source at all.
    uri_template: str = ""

    def default_uri(self, args: Mapping[str, Any]) -> str | None:
        """`uri_template` filled from the call's arguments, or None if none is declared."""
        if not self.uri_template:
            return None
        try:
            return self.uri_template.format(tool=self.name, **args)
        except (KeyError, IndexError):
            # A template naming an argument this call did not pass would otherwise render with the
            # placeholder still in it -- a citation pointing at `{source}`. Fall back to something
            # that at least identifies the tool.
            return f"{self.uri_template.split('{', 1)[0]}{self.name}"


@dataclass(frozen=True)
class ProcessPack:
    """One reconciliation-shaped process."""

    key: str
    name: str
    #: Namespaced, e.g. "nav.fx_rate". Namespacing is enforced at registration so two processes
    #: cannot collide on a bare category name.
    capabilities: tuple[str, ...]
    #: Where this process keeps its agent manifests. Sourced from the pack rather than from
    #: inside the registry, so adding a process adds no file under registry/.
    manifest_dir: Path
    #: Where this process keeps its prompt templates, one per agent id. Declared by the pack for the
    #: same reason as the manifests: a second process ships its own instructions without any change
    #: here. Defaults to a `prompts` directory beside the manifests.
    prompt_dir: Path | None = None
    tools: tuple[ToolSpec, ...] = ()
    #: One threshold set per unit this process measures impact in.
    thresholds: tuple[ThresholdSet, ...] = ()
    #: Human-readable unit for the process's control total, for reports.
    control_total_unit: str = ""
    #: Per capability, the **facts** a verdict must cite before it may assert a cause.
    #: `("nav.fx_rate", ("rate", "rate_date"))` says: an FX verdict has to name the rate it
    #: believes was applied *and* the date that rate belongs to, because a stale-rate break is
    #: precisely the gap between them.
    #:
    #: Facts, not tool namespaces, and the difference is the whole control. Requiring a namespace
    #: only asks that *some* call was made to it -- measured, a GBP lookup for an unrelated July
    #: date that returned nothing satisfied a namespace requirement while the verdict's every
    #: number was invented. Requiring facts means the observation cited must actually carry them.
    #:
    #: A tuple of pairs rather than a dict because a dict on a frozen dataclass is mutable through
    #: the reference the pack hands out, and this is a governance rule.
    #:
    #: Declared by the process, checked once in the control plane. A second process states its own
    #: and inherits the check, which is the extensibility claim as a mechanism rather than a
    #: promise.
    evidence_requirements: tuple[tuple[str, tuple[str, ...]], ...] = ()
    #: Capabilities an agent of this process may **request from another process**, through
    #: `gateway.delegate`. Empty for every process that does not coordinate, which is most of them.
    #:
    #: Declared on the pack rather than in an agent's manifest, and not only to keep `registry/`
    #: untouched: delegation is a statement about how two *departments* are permitted to interact,
    #: and putting it in a manifest would let one agent's own document widen the coupling between
    #: them. A pack is the smallest thing that can honestly own that decision.
    delegations: tuple[str, ...] = ()
    notes: str = ""

    def declared_facts(self) -> frozenset[str]:
        """Every fact name any of this process's tools can produce."""
        return frozenset(name for spec in self.tools for name in spec.facts)

    def evidence_requirement(self, capability: str) -> tuple[str, ...]:
        """Facts a verdict must cite for this capability. Empty means nothing is mandated."""
        for declared, namespaces in self.evidence_requirements:
            if declared == capability:
                return namespaces
        return ()

    def tools_by_name(self) -> dict[str, ToolSpec]:
        return {t.name: t for t in self.tools}

    def __post_init__(self) -> None:
        for cap in self.capabilities:
            if not cap.startswith(f"{self.key}."):
                raise ValueError(
                    f"capability {cap!r} is not namespaced to process {self.key!r}. "
                    f"Unnamespaced capabilities collide between processes."
                )
        _validate_evidence_requirements(self)


def platform_facts() -> frozenset[str]:
    """Facts projected by tools every process may use."""
    return frozenset(name for spec in _platform_tools.values() for name in spec.facts)


def _validate_producible_facts(pack: ProcessPack) -> None:
    """Refuse a requirement no tool could ever satisfy. **Checked at registration, not construction.**

    Producibility depends on what is registered: a pack's own tools *plus the platform's*, because a
    platform tool is reachable by any agent whose manifest allows it and a fact it projects is
    therefore citable. Platform tools are registered by the composition root, which necessarily runs
    after a pack module is imported -- so checking this in `__post_init__` measured an empty
    catalogue and refused every requirement over a shared capability. The only workaround would have
    been duplicating the tool into the pack, which `register` separately refuses as a name collision.

    The *shape* of a requirement -- a capability this pack declares, no duplicates, not empty -- is
    knowable at construction and stays there, because construction is bypassable.
    """
    producible = pack.declared_facts() | platform_facts()
    for capability, required in pack.evidence_requirements:
        unknown = sorted(set(required) - producible)
        if unknown:
            raise ValueError(
                f"process {pack.key!r} requires fact(s) {unknown} for {capability!r}, which no "
                f"registered tool can produce -- no verdict could ever satisfy it. Producible: "
                f"{sorted(producible)}"
            )


def _validate_evidence_requirements(pack: ProcessPack) -> None:
    """Refuse a requirement that can never bind.

    Both failure modes are typos, and both are silent without this: a capability this pack does not
    declare means the rule is attached to nothing, and a namespace no tool of this pack uses means
    no verdict can ever satisfy it. The first weakens a governance rule to nothing while looking
    present -- the shape this project has already had to fix once, where a policy got weaker while
    the commit said it got stronger.
    """
    for spec in pack.tools:
        if spec.observe is not None and not spec.facts:
            raise ValueError(
                f"{spec.name!r} projects observations but declares no `facts`, so nothing it "
                f"produces could ever be required, validated, or cited."
            )

    declared = set(pack.capabilities)
    seen: set[str] = set()
    for capability, required in pack.evidence_requirements:
        if capability in seen:
            raise ValueError(
                f"process {pack.key!r} declares two evidence requirements for {capability!r}; "
                f"only the first would ever be read"
            )
        seen.add(capability)
        if capability not in declared:
            raise ValueError(
                f"process {pack.key!r} requires evidence for {capability!r}, which it does not "
                f"declare as a capability. The rule would bind to nothing. Declared: "
                f"{sorted(declared)}"
            )
        if not required:
            raise ValueError(
                f"process {pack.key!r} declares an empty evidence requirement for {capability!r}. "
                f"Omit the entry instead, so 'no requirement' is stated once."
            )


class UnknownTool(KeyError):
    """A name absent from every registered pack.

    Distinct from a policy denial: a denial means the agent may not use a real tool, this means
    no such tool exists. Collapsing them would make a manifest typo read as a permissions
    problem.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)

    def __str__(self) -> str:
        # KeyError.__str__ applies repr() to its argument, which would quote the message.
        return self.args[0] if self.args else ""


_packs: dict[str, ProcessPack] = {}
_overrides: dict[str, ToolSpec] = {}
#: Tools the platform itself offers to every process — registry discovery, for instance. Held
#: outside the packs because two processes both needing registry lookup would otherwise collide
#: on the tool name, and whichever registered second would fail.
_platform_tools: dict[str, ToolSpec] = {}
#: Called after any registration change, so a caller cannot forget to invalidate the discovery
#: cache and leave a registered pack's agents invisible.
_change_hooks: list[Callable[[], None]] = []


def on_change(hook: Callable[[], None]) -> None:
    _change_hooks.append(hook)


def _on_change() -> None:
    for hook in _change_hooks:
        hook()


def register_platform_tools(*specs: ToolSpec) -> None:
    """Register tools every process may declare. Called by the composition root, which is the
    only place that may import both the control plane and the modules these wrap."""
    unsourced = sorted(spec.name for spec in specs if not spec.source.strip())
    if unsourced:
        raise ValueError(
            f"platform tool(s) {unsourced} declare no `source`. Every citation must name where its "
            f"evidence came from; pack tools are checked in `register` and these were not."
        )
    for spec in specs:
        _platform_tools[spec.name] = spec
    _on_change()


class DuplicateProcess(ValueError):
    pass


def _prompt_dir_of(pack: ProcessPack) -> Path:
    """Where a pack's templates live, whether or not it names the directory explicitly.

    One definition, used by `prompt_dirs` and by the collision check below. Two definitions is how
    that check first shipped broken: it read `pack.prompt_dir` alone, the fund-accounting pack leaves
    that `None` and relies on this fallback, so the check saw an empty set for the very pack whose
    `investigator.md` it existed to protect -- and a hijacking pack registered cleanly.
    """
    return pack.prompt_dir or pack.manifest_dir.parent / "prompts"


def _prompt_names(pack: ProcessPack) -> set[str]:
    """The template filenames a pack ships, or nothing if it ships no directory."""
    directory = _prompt_dir_of(pack)
    if not directory.is_dir():
        return set()
    return {path.name for path in directory.glob("*.md")}


def register(pack: ProcessPack) -> None:
    """Register a process.

    Validation happens here as well as in `ProcessPack.__post_init__`, because construction is
    bypassable: a duck-typed object or a subclass overriding `__post_init__` would otherwise
    register un-namespaced capabilities.
    """
    if not isinstance(pack, ProcessPack):
        raise TypeError(f"expected a ProcessPack, got {type(pack).__name__}")
    for cap in pack.capabilities:
        if not cap.startswith(f"{pack.key}."):
            raise ValueError(f"capability {cap!r} is not namespaced to process {pack.key!r}")
    if pack.key in _packs and _packs[pack.key] is not pack:
        raise DuplicateProcess(f"process {pack.key!r} is already registered")
    _validate_evidence_requirements(pack)
    _validate_producible_facts(pack)
    unsourced = sorted(spec.name for spec in pack.tools if not spec.source.strip())
    if unsourced:
        raise ValueError(
            f"process {pack.key!r} declares tool(s) {unsourced} with no `source`. Every citation "
            f"must name where its evidence came from, and the platform cannot invent that."
        )


    for other in _packs.values():
        if other.key == pack.key:
            continue
        clash = set(other.tools_by_name()) & set(pack.tools_by_name())
        if clash:
            raise DuplicateProcess(f"tool names claimed by two processes: {sorted(clash)}")
        # Two packs claiming the same unit with different thresholds would let alphabetical
        # ordering decide whose governance applies to whose cases, silently.
        units = {t.unit for t in other.thresholds} & {t.unit for t in pack.thresholds}
        if units:
            raise DuplicateProcess(
                f"processes {other.key!r} and {pack.key!r} both declare thresholds for unit(s) "
                f"{sorted(units)}. Threshold resolution is by unit, so this would make one "
                f"process's materiality govern the other's cases."
            )
        # And the same reasoning for prompt filenames. `prompts.path_for` walks the registered
        # directories and takes the first hit, and `registered()` sorts by key -- so two packs
        # shipping `investigator.md` would let alphabetical ordering decide which process's
        # instructions an agent is given, silently. Measured before this check: a pack keyed "aml"
        # shipping `prompts/investigator.md` captured the fund fleet's template.
        shared = _prompt_names(other) & _prompt_names(pack)
        if shared:
            raise DuplicateProcess(
                f"processes {other.key!r} and {pack.key!r} both ship prompt template(s) "
                f"{sorted(shared)}. Templates resolve by filename across every registered process, "
                f"so this would decide by ordering which instructions an agent receives."
            )

    _packs[pack.key] = pack
    _on_change()


def delegations_for(capabilities: Iterable[str]) -> tuple[str, ...]:
    """What an agent handling these capabilities is permitted to request from other processes.

    Resolved from the pack that owns each capability, so the permission follows the *process* the
    agent belongs to. Takes capabilities rather than an agent reference deliberately: looking an
    agent up would mean this module reaching into the registry, and the control plane's tool
    catalogue has no business knowing how identities are published.
    """
    permitted: set[str] = set()
    for capability in capabilities:
        pack = process_of(capability)
        if pack is not None:
            permitted.update(pack.delegations)
    return tuple(sorted(permitted))


def evidence_requirement_for(capability: str) -> tuple[str, ...]:
    """The declared requirement for a capability, from whichever process owns it.

    Resolved by capability rather than by process key so the caller need not know which pack owns
    what -- the same reason `resolve()` looks tools up by name. An unowned capability requires
    nothing, which is the honest answer: a process that declares no rule has not made one.
    """
    for pack in _packs.values():
        if capability in pack.capabilities:
            return pack.evidence_requirement(capability)
    return ()


def registered() -> tuple[ProcessPack, ...]:
    return tuple(_packs[k] for k in sorted(_packs))


def clear() -> None:
    """Reset the registry. For tests and the composition root only."""
    _packs.clear()
    _platform_tools.clear()
    _on_change()


def capabilities() -> tuple[str, ...]:
    """Every capability any registered process declares.

    The capability universe comes from packs, not from published manifests. If it came from
    manifests, a capability with no authorised agent would simply vanish from coverage instead
    of reporting NONE — and refusing to route an unsupported capability is a governance
    demonstration, not a gap to hide.
    """
    return tuple(sorted({c for p in registered() for c in p.capabilities}))


def manifest_dirs() -> tuple[Path, ...]:
    return tuple(p.manifest_dir for p in registered() if p.manifest_dir.is_dir())


def prompt_dirs() -> tuple[Path, ...]:
    """Every registered process's prompt directory.

    Ordered by process key, because `registered()` sorts -- this said "in registration order",
    which it never was. That order would decide which process's template an agent received if two
    shipped the same filename, so `register` refuses the collision outright rather than leaving the
    answer to sorting.
    """
    return tuple(directory for p in registered() if (directory := _prompt_dir_of(p)).is_dir())


def thresholds_for(unit: str) -> ThresholdSet | None:
    for pack in registered():
        for t in pack.thresholds:
            if t.unit == unit:
                return t
    return None


def process_of(capability: str) -> ProcessPack | None:
    for pack in registered():
        if capability in pack.capabilities:
            return pack
    return None


def catalogue() -> MappingProxyType[str, ToolSpec]:
    """Read-only view of every registered tool.

    A mutable mapping here would let in-process code swap a spec and run arbitrary code under a
    declared tool's label. Tests use `override()`, which is scoped and named.
    """
    merged: dict[str, ToolSpec] = dict(_platform_tools)
    for pack in registered():
        merged.update(pack.tools_by_name())
    return MappingProxyType(merged)


def resolve(name: str) -> ToolSpec:
    spec = _overrides.get(name) or catalogue().get(name)
    if spec is None:
        raise UnknownTool(
            f"{name!r} is not in any registered process pack. Add a ToolSpec to the pack's "
            f"tools; it cannot be supplied at call time."
        )
    if spec.name != name:
        raise UnknownTool(
            f"key {name!r} maps to a spec named {spec.name!r}; refusing to execute under a "
            f"mismatched label"
        )
    return spec


@contextmanager
def override(name: str, spec: ToolSpec) -> Iterator[None]:
    """Temporarily substitute one tool. Tests only.

    Gated on the test runner rather than trusted to convention. `resolve()` consults `_overrides`
    first, so this seam runs arbitrary code under a declared tool's label while the audit record
    names the declared tool and logs ALLOW — precisely the property B1 was closed to remove.
    "Scoped and named" was naming, not a control.
    """
    if "PYTEST_CURRENT_TEST" not in os.environ:
        raise RuntimeError(
            "packs.override is a test seam. Outside the test runner it would let code execute "
            "under another tool's name while the audit log records that name — the exact defect "
            "B1 closed."
        )
    previous = _overrides.get(name)
    _overrides[name] = spec
    try:
        yield
    finally:
        if previous is None:
            _overrides.pop(name, None)
        else:
            _overrides[name] = previous
