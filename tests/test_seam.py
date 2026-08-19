"""The seam between the control plane and a process, asserted rather than described.

Every claim here was previously verified by hand with a throwaway script and guarded by nothing.
That is the pattern this project keeps getting caught by: a property demonstrated once, deleted,
and then quietly broken. These tests are also the only evidence for the platform claim that
survives the de-scope ladder -- the second process pack and the demo can both be cut, and this
cannot.

Four properties:

  1. No module under `control_plane/` or `registry/` reaches a process package, **transitively**.
     One-hop checking misses `gateway -> tools -> domain` and `identity -> registry -> domain`.
  2. The control plane reads only `CaseFacts` fields off its case parameter. An **allow-list**,
     not a deny-list: a deny-list of named members was both incomplete and self-colliding, and
     could not cover members added later.
  3. A process that does not exist in the codebase can be driven through the control plane.
  4. Its audit record has the same shape as a NAV case's. "The same governance log runs over it"
     means the span keys match, not merely that a span exists.
"""

from __future__ import annotations

import ast
import tempfile
import textwrap
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from nav_sentinel import composition
from nav_sentinel.control_plane import approvals, audit, gateway, identity, packs, policies
from nav_sentinel.control_plane.governance import ApprovalClass, CaseFacts, Impact, ThresholdSet
from nav_sentinel.registry import discover

SRC = Path(__file__).resolve().parents[1] / "src" / "nav_sentinel"

#: Packages that belong to a process rather than to the platform. The control plane may not reach
#: any of them, by any route.
PROCESS_PACKAGES = ("domain", "tools", "agents", "pipeline", "memory")
PLATFORM_PACKAGES = ("control_plane", "registry")


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC).with_suffix("")
    name = "nav_sentinel." + str(rel).replace("/", ".")
    return name.removesuffix(".__init__")


def _import_graph() -> dict[str, set[str]]:
    """Every intra-package import, including those inside `if TYPE_CHECKING:` and function bodies.

    `ast.walk` descends into both, deliberately: a local import is still a dependency, and
    exempting `TYPE_CHECKING` is the first thing an implementer reaches for to make a check like
    this pass.
    """
    graph: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        edges: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("nav_sentinel"):
                    edges.add(node.module)
            elif isinstance(node, ast.Import):
                edges |= {a.name for a in node.names if a.name.startswith("nav_sentinel")}
            elif isinstance(node, ast.Call):
                # importlib.import_module("nav_sentinel.domain...") would evade the above.
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name in {"import_module", "__import__"}:
                    edges |= {
                        a.value for a in node.args
                        if isinstance(a, ast.Constant)
                        and isinstance(a.value, str)
                        and a.value.startswith("nav_sentinel")
                    }
        graph[_module_name(path)] = edges
    return graph


def _reachable(graph: dict[str, set[str]], start: str) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        for nxt in graph.get(stack.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _layer(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else ""


class TestControlPlaneDoesNotReachAProcess:
    def test_no_transitive_path_from_the_platform_to_a_process(self):
        graph = _import_graph()
        violations: dict[str, list[str]] = {}
        for module in sorted(graph):
            if _layer(module) not in PLATFORM_PACKAGES:
                continue
            reached = sorted(
                m for m in _reachable(graph, module) if _layer(m) in PROCESS_PACKAGES
            )
            if reached:
                violations[module] = reached
        assert not violations, (
            "the control plane reaches a process package:\n"
            + "\n".join(f"  {k} -> {v}" for k, v in violations.items())
        )

    def test_the_graph_actually_found_edges(self):
        """A check that silently sees nothing passes forever. Pin that the graph is populated and
        that a known process-side edge exists, so the test above cannot be vacuous."""
        graph = _import_graph()
        assert len(graph) > 15, f"only {len(graph)} modules parsed"
        assert any(edges for edges in graph.values())
        assert "nav_sentinel.control_plane.packs" in graph["nav_sentinel.domain.pack"], (
            "expected the NAV pack to import the control plane's port"
        )

    def test_the_composition_root_is_outside_both_packages(self):
        """Something must import both sides to wire them. If that lived inside the control plane
        the seam would be decorative."""
        graph = _import_graph()
        root = "nav_sentinel.composition"
        assert _layer(root) not in PLATFORM_PACKAGES + PROCESS_PACKAGES
        reached = _reachable(graph, root)
        assert any(_layer(m) in PLATFORM_PACKAGES for m in reached)
        assert any(_layer(m) in PROCESS_PACKAGES for m in reached)


class TestControlPlaneReadsOnlyCaseFacts:
    """An allow-list over `CaseFacts.model_fields`. Complete by construction, immune to new
    domain members, and it cannot be defeated by adding a field to the domain."""

    ALLOWED = set(CaseFacts.model_fields) | {"as_span_attributes", "model_dump", "model_copy"}
    PARAM_NAMES = {"case", "facts"}

    def _accesses(self) -> dict[str, set[str]]:
        found: dict[str, set[str]] = {}
        for path in sorted((SRC / "control_plane").glob("*.py")):
            attrs: set[str] = set()
            for node in ast.walk(ast.parse(path.read_text())):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in self.PARAM_NAMES
                ):
                    attrs.add(node.attr)
            if attrs:
                found[path.name] = attrs
        return found

    def test_every_attribute_read_is_a_declared_field(self):
        outside = {
            name: sorted(attrs - self.ALLOWED)
            for name, attrs in self._accesses().items()
            if attrs - self.ALLOWED
        }
        assert not outside, (
            "the control plane reads members CaseFacts does not declare:\n"
            + "\n".join(f"  {k}: {v}" for k, v in outside.items())
            + "\nEvery one is a domain member reachable only because a process handed over a "
            "domain object."
        )

    def test_the_scan_is_not_vacuous(self):
        """It must actually be reading something, or it would pass on an empty result."""
        accesses = self._accesses()
        assert accesses, "no case-parameter attribute reads found at all"
        assert "impact" in set().union(*accesses.values())

    def test_no_getattr_on_the_case_parameter(self):
        """`getattr(facts, "fund_id")` is invisible to an attribute scan."""
        offenders = []
        for path in sorted((SRC / "control_plane").glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "getattr"
                    and node.args
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id in self.PARAM_NAMES
                ):
                    offenders.append(f"{path.name}:{node.lineno}")
        assert not offenders, f"getattr on a case parameter: {offenders}"

    def test_case_facts_admits_no_domain_enum(self):
        """Pydantic coerces a StrEnum to str. A frozen dataclass would pass one through intact
        with `.value` live, satisfying every annotation and every static check."""
        from nav_sentinel.domain.models import BreakCategory, ExceptionStatus

        facts = CaseFacts(
            case_id="C", subject_id="S", as_of=date(2026, 8, 17),
            capability=BreakCategory.FX_RATE, status=ExceptionStatus.OPEN,
            impact=Impact(value=Decimal(1), unit="bps"),
        )
        assert type(facts.status) is str
        assert type(facts.capability) is str
        assert not hasattr(facts.status, "value")


# --------------------------------------------------------------------------------------------
# A process that does not exist in the codebase.
#
# Deliberately not fund accounting and not transfer agency: its control total is denominated in
# documents, so nothing about it could be satisfied by a code path that secretly assumes basis
# points. If the control plane can host this, it can host anything reconciliation-shaped.

SYNTHETIC_MANIFEST = """
agent_id: kyc-refresh-investigator
version: 1.0.0
display_name: KYC refresh investigator
owner: synthetic-test
description: Investigates investor files missing a current identity document.
handles_capabilities: [kyc.document_expired]
model: gemini-3.7-flash
allowed_tools: [registry.coverage]
data_scopes: {read: [registry], write: []}
authority: {may_propose_remediation: false, may_post_entries: false}
untrusted_inputs: false
requires_model_armor: false
"""


@pytest.fixture
def synthetic_process():
    """Register a process defined entirely in this test file, then restore the real registry."""
    directory = Path(tempfile.mkdtemp())
    (directory / "kyc-refresh.yaml").write_text(textwrap.dedent(SYNTHETIC_MANIFEST).strip() + "\n")

    pack = packs.ProcessPack(
        key="kyc",
        name="KYC refresh",
        capabilities=("kyc.document_expired", "kyc.address_unverified"),
        manifest_dir=directory,
        thresholds=(
            ThresholdSet(
                unit="documents",
                auto_clear_below=Decimal(1),
                single_reviewer_below=Decimal(25),
                four_eyes_below=Decimal(250),
            ),
        ),
        control_total_unit="documents",
    )
    packs.register(pack)
    try:
        yield pack
    finally:
        composition.reset()
        composition.configure()


def _facts(capability: str, value: str, unit: str, case_id: str = "SYN-1") -> CaseFacts:
    return CaseFacts(
        case_id=case_id,
        subject_id="INVESTOR-4471",
        as_of=date(2026, 8, 17),
        capability=capability,
        impact=Impact(value=Decimal(value), unit=unit),
        status="triaged",
        severity="medium",
        item_count=3,
    )


class TestASecondProcessRunsThroughTheControlPlane:
    def test_its_agent_is_discovered_by_capability(self, synthetic_process):
        agent = discover.discover_for_capability("kyc.document_expired")
        assert agent is not None, "the synthetic process's manifest was not loaded"
        assert agent.ref == "kyc-refresh-investigator@1.0.0"

    def test_an_uncovered_capability_reports_none_rather_than_routing(self, synthetic_process):
        """Refusing to route an unsupported capability is a governance outcome worth surfacing,
        which is why the capability universe comes from packs rather than from manifests."""
        coverage = discover.coverage()
        assert coverage["kyc.document_expired"] == "kyc-refresh-investigator@1.0.0"
        assert coverage["kyc.address_unverified"] is None
        assert discover.discover_for_capability("kyc.address_unverified") is None

    def test_the_band_is_derived_from_its_own_unit(self, synthetic_process):
        """Nothing here is denominated in basis points. A code path that assumed them would
        escalate everything instead of banding."""
        for count, expected in (
            ("0", ApprovalClass.AUTO_CLEAR),
            ("3", ApprovalClass.SINGLE_REVIEWER),
            ("40", ApprovalClass.FOUR_EYES),
            ("900", ApprovalClass.CIO_ESCALATION),
        ):
            facts = _facts("kyc.document_expired", count, "documents")
            assert policies.band_for(facts.impact) is expected, count

    def test_a_tool_call_is_policed_the_same_way(self, synthetic_process):
        gateway.clear_decision_log()
        with identity.acting_as("kyc-refresh-investigator"):
            gateway.call_tool("registry.coverage")
        recorded = [(d.policy_id, d.effect.value) for d in gateway.decision_log()]
        assert ("P-001-TOOL-ALLOWLIST", "allow") in recorded
        assert ("P-006-DATA-SCOPE", "allow") in recorded

    def test_an_undeclared_tool_is_denied_the_same_way(self, synthetic_process):
        with identity.acting_as("kyc-refresh-investigator"):
            with pytest.raises(policies.PolicyViolation) as exc:
                gateway.call_tool("books_and_records.positions", "accounting")
        assert exc.value.decision.policy_id == "P-001-TOOL-ALLOWLIST"

    def test_it_cannot_post_either(self, synthetic_process):
        facts = _facts("kyc.document_expired", "900", "documents")
        with identity.acting_as("kyc-refresh-investigator"):
            with pytest.raises(policies.PolicyViolation) as exc:
                gateway.authorize_posting(facts, None)
        assert exc.value.decision.policy_id == "P-003-NO-AUTONOMOUS-POSTING"

    def test_an_approval_binds_to_its_band_here_too(self, synthetic_process):
        store = approvals.InMemoryApprovalStore()
        approvals.use_store(store)
        authority = approvals.ApprovalAuthority(store)
        poster = discover.get("kyc-refresh-investigator").model_copy(
            update={"authority": discover.get("kyc-refresh-investigator").authority.model_copy(
                update={"may_post_entries": True}
            )}
        )
        facts = _facts("kyc.document_expired", "900", "documents")   # cio_escalation
        wrong = authority.grant(
            facts.case_id, ApprovalClass.SINGLE_REVIEWER,
            (approvals.Principal(subject="cal", role="reviewer"),),
        )
        assert not policies.may_post_entry(poster, facts, wrong.ref).allowed

        right = authority.grant(
            facts.case_id, ApprovalClass.CIO_ESCALATION,
            (approvals.Principal(subject="ada", role="cio"),),
        )
        assert policies.may_post_entry(poster, facts, right.ref).allowed


class TestTheAuditRecordHasTheSameShape:
    """"The same governance log runs over it" means the span keys match, not that a span exists."""

    def _root_keys(self, spans, facts) -> set[str]:
        spans.clear()
        with audit.case_trace(facts) as (_span, _trace_id):
            pass
        roots = [s for s in spans.get_finished_spans() if s.name == "nav_sentinel.exception_case"]
        assert roots, "no case span emitted"
        return set(roots[-1].attributes)

    def test_a_synthetic_case_and_a_nav_case_emit_the_same_attribute_keys(
        self, spans, synthetic_process
    ):
        synthetic = self._root_keys(spans, _facts("kyc.document_expired", "40", "documents"))
        nav = self._root_keys(
            spans, _facts("nav.fx_rate", "5.41", "bps", case_id="NAV-1")
        )
        assert synthetic == nav, (
            f"audit record differs by process. only in synthetic: {sorted(synthetic - nav)}; "
            f"only in nav: {sorted(nav - synthetic)}"
        )

    def test_the_keys_are_owned_by_the_control_plane(self, spans, synthetic_process):
        """A mapping handed in by the process would put these names under process control."""
        keys = self._root_keys(spans, _facts("kyc.document_expired", "40", "documents"))
        assert all(k.startswith("nav.") for k in keys), sorted(keys)
        assert "nav.case.impact_unit" in keys
        assert "nav.case.approval_class" in keys
