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
from collections.abc import Callable, Iterator, Mapping
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
    observe: Callable[[Any], Mapping[str, object]] | None = None


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
    tools: tuple[ToolSpec, ...] = ()
    #: One threshold set per unit this process measures impact in.
    thresholds: tuple[ThresholdSet, ...] = ()
    #: Human-readable unit for the process's control total, for reports.
    control_total_unit: str = ""
    notes: str = ""

    def tools_by_name(self) -> dict[str, ToolSpec]:
        return {t.name: t for t in self.tools}

    def __post_init__(self) -> None:
        for cap in self.capabilities:
            if not cap.startswith(f"{self.key}."):
                raise ValueError(
                    f"capability {cap!r} is not namespaced to process {self.key!r}. "
                    f"Unnamespaced capabilities collide between processes."
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
    for spec in specs:
        _platform_tools[spec.name] = spec
    _on_change()


class DuplicateProcess(ValueError):
    pass


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

    _packs[pack.key] = pack
    _on_change()


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
