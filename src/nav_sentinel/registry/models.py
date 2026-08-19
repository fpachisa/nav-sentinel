"""Agent Registry schema.

A manifest is the single authoritative declaration of what an agent is and what it may do.
Three things read it, which is what keeps it honest rather than decorative:

  * Triage, at runtime, to discover which specialist handles a given capability.
  * The Agent Gateway, on every tool call, to deny anything outside `allowed_tools`.
  * Deployment, to mint one service account per agent with exactly these data scopes.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from nav_sentinel.control_plane import packs
from nav_sentinel.control_plane.governance import Impact


class Authority(BaseModel):
    may_propose_remediation: bool = False
    may_post_entries: bool = False
    #: The largest impact this agent may clear without a human, tagged with its unit. Was
    #: `max_autonomous_bps` — a basis-point field in a process-agnostic registry, meaningless to
    #: a process whose control total is denominated in shares. Zero means no autonomy at any size.
    max_autonomous_impact: Impact | None = None

    def clears_autonomously(self, impact: Impact) -> bool:
        ceiling = self.max_autonomous_impact
        if ceiling is None or ceiling.unit != impact.unit:
            return False
        return abs(impact.value) <= abs(ceiling.value)


class DataScopes(BaseModel):
    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)


class Sla(BaseModel):
    target_latency_seconds: int = 60


class AgentManifest(BaseModel):
    agent_id: str
    version: str
    display_name: str
    owner: str
    description: str
    #: Namespaced capability strings, e.g. "nav.fx_rate". Was a closed enum of one domain's
    #: categories, which could never route for a second process.
    handles_capabilities: list[str] = Field(default_factory=list)
    model: str
    allowed_tools: list[str] = Field(default_factory=list)
    data_scopes: DataScopes = Field(default_factory=DataScopes)
    authority: Authority = Field(default_factory=Authority)
    untrusted_inputs: bool = False
    requires_model_armor: bool = False
    sla: Sla = Field(default_factory=Sla)

    @property
    def ref(self) -> str:
        """Stable, version-pinned reference recorded in every audit trail."""
        return f"{self.agent_id}@{self.version}"

    @property
    def service_account_id(self) -> str:
        """Per-agent identity. Truncated to Google's 30-character limit for account ids."""
        return f"nav-{self.agent_id}"[:30].rstrip("-")


def load_manifests(directories: tuple[Path, ...] | Path | None = None) -> list[AgentManifest]:
    """Load every published manifest.

    Manifests are sourced from the *registered process packs*, not from a directory inside this
    package. That matters for a specific reason: while `MANIFEST_DIR` pointed at
    `registry/manifests/`, adding a process meant adding a file under `registry/` — which would
    have falsified the claim that a new process changes nothing here.
    """
    if directories is None:
        if not packs.registered():
            raise RuntimeError(
                "No process pack is registered, so there are no manifests to load. Call "
                "nav_sentinel.composition.configure() at your entry point. Returning an empty "
                "registry here would look indistinguishable from a fleet with no agents."
            )
        dirs = packs.manifest_dirs()
    elif isinstance(directories, Path):
        dirs = (directories,)
    else:
        dirs = directories

    manifests: list[AgentManifest] = []
    seen: dict[str, Path] = {}
    for directory in dirs:
        for path in sorted(directory.glob("*.yaml")):
            manifest = AgentManifest.model_validate(yaml.safe_load(path.read_text()))
            if manifest.ref in seen:
                raise ValueError(
                    f"{manifest.ref} is published twice: {seen[manifest.ref]} and {path}"
                )
            seen[manifest.ref] = path
            manifests.append(manifest)
    return manifests


def load_manifest(agent_id: str, directory: Path | None = None) -> AgentManifest:
    for m in load_manifests(directory):
        if m.agent_id == agent_id:
            return m
    raise KeyError(f"no manifest for agent_id={agent_id!r}")
