"""Invariants of the control plane. These are the controls that make the fleet deployable;
if any of them regresses the project is no longer safe to run against a real fund."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from nav_sentinel.control_plane import approvals, gateway, identity, packs, policies
from nav_sentinel.control_plane.governance import CaseFacts, Impact
from nav_sentinel.control_plane.policies import PolicyViolation
from nav_sentinel.domain.models import BreakType, ExceptionCase, ReconciliationBreak
from nav_sentinel.registry import discover
from nav_sentinel.registry.models import Authority, load_manifests

NAV_DATE = date(2026, 8, 17)


@pytest.fixture(autouse=True)
def _clear_log():
    gateway.clear_decision_log()
    yield


@pytest.fixture
def case():
    return ExceptionCase(
        case_id="CASE-TEST-0001", fund_id="F1", as_of=NAV_DATE,
        breaks=[
            ReconciliationBreak(
                break_id="BRK-1", fund_id="F1", as_of=NAV_DATE,
                break_type=BreakType.MARKET_VALUE, isin="ISIN1",
                accounting_value=Decimal(1000), custodian_value=Decimal(900),
                tolerance_applied=Decimal(1),
            )
        ],
    )


class TestRegistry:
    def test_declared_capabilities_without_an_investigator_are_exactly_the_known_gaps(self):
        """Inverted, not deleted, and the inversion is the point.

        This asserted full coverage and passed -- which read as completeness and was in fact the
        absence of a control. Two acceptance criteria require that a break can be triaged
        correctly and then **refused for want of an authorised investigator**: the adversarial
        pricing case is classified `nav.pricing`, the registry reports nothing authorised to handle
        it, and the case escalates to a human. While every capability had an investigator that path
        was unreachable.

        So the gap is now asserted rather than tolerated, and pinned to an exact set: a *new*
        uncovered capability is still a failure, and re-publishing either manifest without
        revisiting those criteria is also a failure.
        """
        gaps = {cat for cat, ref in discover.coverage().items() if ref is None}
        assert gaps == {"nav.pricing", "nav.cash_fees"}, (
            f"coverage gaps are {sorted(gaps)}; expected exactly the two unpublished capabilities. "
            f"See src/nav_sentinel/domain/manifests/unpublished/README.md"
        )

    def test_the_unpublished_manifests_are_retained_and_not_loaded(self):
        """Publication must be a controlled act for identity resolution to mean anything, so the
        two are moved rather than deleted -- and the loader must not recurse into the directory
        holding them."""
        from nav_sentinel.control_plane import packs

        unpublished = next(iter(packs.manifest_dirs())) / "unpublished"
        assert unpublished.is_dir()
        retained = {p.stem for p in unpublished.glob("*.yaml")}
        assert retained == {"pricing-investigator", "cash-fees-investigator"}, retained

        loaded = {m.agent_id for m in load_manifests()}
        assert loaded.isdisjoint(retained), f"an unpublished manifest was loaded: {loaded & retained}"

    def test_service_account_ids_fit_google_limit(self):
        for m in load_manifests():
            assert len(m.service_account_id) <= 30, f"{m.agent_id} exceeds the 30-char limit"

    def test_discovery_selects_highest_version(self):
        m = discover.discover_for_capability("nav.corporate_action")
        assert m is not None and m.agent_id == "corporate-actions-investigator"

    def test_unclassified_has_no_specialist(self):
        """Triage handles unclassified work; no investigator should claim it."""
        specialists = [
            m for m in load_manifests()
            if "nav.unclassified" in m.handles_capabilities
            and m.agent_id != "triage-agent"
        ]
        assert specialists == []


class TestNoAgentHoldsPostingAuthority:
    """The hard control. If this test ever passes with a non-empty list, an agent can move
    a fund's NAV without a human, and the project must not ship."""

    def test_no_published_agent_may_post_entries(self):
        offenders = [m.ref for m in load_manifests() if m.authority.may_post_entries]
        assert offenders == [], f"agents holding posting authority: {offenders}"

    def test_no_published_agent_has_autonomous_headroom(self):
        """The ceiling is now enforced by P-003, not merely declared. A null ceiling means no
        adjustment of any size clears without a human, in any unit."""
        for m in load_manifests():
            assert m.authority.max_autonomous_impact is None, (
                f"{m.ref} declares an autonomous ceiling of {m.authority.max_autonomous_impact}"
            )

    def test_posting_is_denied_even_with_a_human_approval(self, case):
        """Approval alone is insufficient: the agent must also hold the authority, and none do."""
        with identity.acting_as("fx-rates-investigator"), pytest.raises(PolicyViolation) as exc:
            gateway.authorize_posting(case.to_facts(), human_approval_ref="APPR-123")
        assert exc.value.decision.policy_id == "P-003-NO-AUTONOMOUS-POSTING"


class TestToolAllowlist:
    def test_declared_tool_is_permitted(self):
        with identity.acting_as("fx-rates-investigator"):
            rate = gateway.call_tool("ecb_fx.latest_rate_on_or_before", "EUR", NAV_DATE)
        assert rate == (NAV_DATE, Decimal(1))

    def test_undeclared_tool_is_denied(self):
        with identity.acting_as("fx-rates-investigator"), pytest.raises(PolicyViolation) as exc:
            gateway.call_tool("books_and_records.cash_movements", "accounting")
        assert exc.value.decision.policy_id == "P-001-TOOL-ALLOWLIST"

    def test_tool_call_without_identity_is_refused(self):
        """No ambient authority: an unattributed tool call fails rather than defaulting."""
        with pytest.raises(identity.IdentityError):
            gateway.call_tool("ecb_fx.rate_on", "USD", NAV_DATE)

    def test_caller_cannot_supply_the_callable(self):
        """B1. The gateway used to take the function as an argument and validate only the
        name, so any callable ran under a declared tool's label while the audit record named
        the label. Resolution now happens in the catalogue and there is no such argument."""
        import inspect

        params = list(inspect.signature(gateway.call_tool).parameters)
        assert params[0] == "tool_name"
        assert "fn" not in params, "call_tool must not accept a callable from the caller"

        sentinel = lambda: "arbitrary code executed"
        with identity.acting_as("fx-rates-investigator"):
            # The old exploit: a declared name with an undeclared function. It now runs the
            # catalogue's function and treats the callable as a positional argument, so the
            # swap cannot execute.
            with pytest.raises(TypeError, match="cannot be supplied by the caller"):
                gateway.call_tool("ecb_fx.rate_on", sentinel)

        # Refused before any policy is evaluated, so no misleading ALLOW is recorded.
        gateway.clear_decision_log()
        with identity.acting_as("fx-rates-investigator"), pytest.raises(TypeError):
            gateway.call_tool("ecb_fx.rate_on", sentinel)
        assert gateway.decision_log() == [], (
            "a rejected call must not leave ALLOW decisions in the audit log"
        )

    def test_unknown_tool_name_is_distinguishable_from_a_denial(self):
        """A typo in a manifest must not read as a permissions problem."""
        with identity.acting_as("fx-rates-investigator"), pytest.raises(packs.UnknownTool):
            gateway.call_tool("ecb_fx.no_such_function")

    def test_every_manifest_tool_resolves_in_the_catalogue(self):
        """A manifest may not declare a capability the catalogue cannot resolve."""
        unresolvable = {
            m.ref: [t for t in m.allowed_tools if t not in packs.catalogue()]
            for m in load_manifests()
        }
        offenders = {k: v for k, v in unresolvable.items() if v}
        assert not offenders, f"manifests declaring phantom tools: {offenders}"

    def test_every_manifest_declares_at_least_one_tool(self):
        for m in load_manifests():
            assert m.allowed_tools, f"{m.ref} declares no tools and could never do work"


class TestDataScopeEnforcement:
    """P-006. `data_scopes` was declared in every manifest, read by bootstrap.sh to pick IAM
    roles, and consulted by no runtime check."""

    def test_tool_outside_declared_scope_is_denied(self):
        """triage declares securities and registry but not positions; grant it the tool anyway and
        the scope check must still refuse it.

        Deliberately the triage agent. This previously used cash-fees, which was unpublished to
        create the "no authorised investigator" gap two acceptance criteria need -- and the test
        broke with it. Triage is the fleet's entry point and cannot be unpublished, so P-006's
        coverage no longer depends on which specialists happen to be published today.
        """
        triage = discover.get("triage-agent")
        assert "positions" not in triage.data_scopes.read, "the asymmetry this test needs is gone"
        widened = triage.model_copy(
            update={"allowed_tools": (*triage.allowed_tools, "books_and_records.positions")}
        )
        decision = policies.tool_within_data_scope(
            widened, "books_and_records.positions", ("positions",)
        )
        assert not decision.allowed
        assert decision.policy_id == "P-006-DATA-SCOPE"

    def test_declared_scope_is_permitted(self):
        triage = discover.get("triage-agent")
        assert policies.tool_within_data_scope(
            triage, "books_and_records.security", ("securities",)
        ).allowed

    def test_external_reference_tools_need_no_internal_scope(self):
        fx = discover.get("fx-rates-investigator")
        assert policies.tool_within_data_scope(fx, "ecb_fx.rate_on", ()).allowed

    def test_every_tool_scope_is_declared_by_its_users(self):
        """Each manifest's tools must be within its own declared scopes -- otherwise the
        manifest is internally inconsistent and P-006 would deny at runtime."""
        problems = []
        for m in load_manifests():
            for name in m.allowed_tools:
                spec = packs.catalogue().get(name)
                if spec is None:
                    continue
                missing = [d for d in spec.reads if d not in m.data_scopes.read]
                if missing:
                    problems.append(f"{m.ref}: {name} needs {missing}")
        assert not problems, problems


class TestDraftingAuthority:
    def test_investigators_may_not_draft(self):
        for m in load_manifests():
            if m.agent_id == "remediation-agent":
                continue
            assert not m.authority.may_propose_remediation, f"{m.ref} may draft entries"

    def test_remediation_agent_may_draft(self):
        with identity.acting_as("remediation-agent"):
            assert gateway.authorize_drafting().allowed


class TestUntrustedIngest:
    def test_agents_reading_the_internet_require_screening(self):
        for m in load_manifests():
            if m.untrusted_inputs:
                assert m.requires_model_armor, (
                    f"{m.ref} ingests untrusted content without Model Armor"
                )

    def test_policy_denies_untrusted_without_armor(self):
        """Constructed case: the policy must refuse the combination, not just the manifests."""
        m = discover.get("corporate-actions-investigator").model_copy(
            update={"requires_model_armor": False}
        )
        decision = policies.untrusted_ingest_requires_armor(m)
        assert not decision.allowed
        assert decision.policy_id == "P-005-UNTRUSTED-INGEST"


class TestDecisionLog:
    def test_decisions_are_recorded_for_audit(self, case):
        with identity.acting_as("fx-rates-investigator"):
            gateway.call_tool("ecb_fx.latest_rate_on_or_before", "EUR", NAV_DATE)
            with pytest.raises(PolicyViolation):
                gateway.authorize_drafting()
        log = gateway.decision_log()
        # P-001 allowlist, P-006 data scope, then the P-002 drafting refusal.
        assert [d.policy_id for d in log] == [
            "P-001-TOOL-ALLOWLIST", "P-006-DATA-SCOPE", "P-002-DRAFT-AUTHORITY",
        ]
        assert [d.effect.value for d in log] == ["allow", "allow", "deny"]


class TestUntrustedOutputScreening:
    """The gateway screens what an untrusted tool returned, rather than trusting an agent to
    remember. These pin the three behaviours the control depends on; before them, a commit
    that added Model-Armor-dependent control flow left the suite still touching none of it."""

    @pytest.fixture
    def stub_armor(self, monkeypatch):
        """Substitute the screener so these run offline. The live service is exercised by
        the `live` variant below; what is under test here is the gateway's wiring."""
        from nav_sentinel.control_plane import model_armor

        def fake_screen(text, *, source_uri=None):
            if "Ignore all previous instructions" in text:
                raise model_armor.ContentBlocked(
                    model_armor.ArmorVerdict(True, "MATCH_FOUND", ("pi_and_jailbreak",)),
                    source_uri,
                )
            return model_armor.ArmorVerdict(False, "NO_MATCH_FOUND")

        monkeypatch.setattr(model_armor, "screen", fake_screen)
        return fake_screen

    @pytest.fixture
    def poison(self):
        from pathlib import Path

        return (Path(__file__).resolve().parents[1]
                / "fixtures" / "data" / "ca_notice_abev_poisoned.txt").read_text()

    def test_tool_output_is_screened_and_the_block_is_audited(self, stub_armor, poison):
        """A block is the fleet's most important governance event. It must appear in the
        decision log, not only as an exception on a trace."""
        from nav_sentinel.control_plane import model_armor

        spec = packs.ToolSpec(
            "edgar.fetch_filing_text", lambda *a, **k: poison, (), untrusted_output=True
        )
        with packs.override("edgar.fetch_filing_text", spec), identity.acting_as("corporate-actions-investigator"):
            with pytest.raises(model_armor.ContentBlocked):
                gateway.call_tool("edgar.fetch_filing_text", "https://sec.gov/x")

        denials = [d for d in gateway.decision_log()
                   if d.effect.value == "deny" and d.policy_id == "P-005-UNTRUSTED-INGEST"]
        assert denials, "the Model Armor block was not recorded in the governance log"
        assert "pi_and_jailbreak" in denials[-1].reason

    @pytest.mark.parametrize("shape", ["dict", "list", "nested"])
    def test_screening_does_not_depend_on_the_container_shape(self, stub_armor, poison, shape):
        """Screening only a bare `str` meant a tool returning a dict or a list bypassed the
        control entirely while still logging ALLOW."""
        from nav_sentinel.control_plane import model_armor

        payloads = {"dict": {"body": poison}, "list": [poison],
                    "nested": {"hits": [{"description": poison}]}}
        spec = packs.ToolSpec(
            "edgar.fetch_filing_text", lambda *a, **k: payloads[shape], (),
            untrusted_output=True,
        )
        with packs.override("edgar.fetch_filing_text", spec), identity.acting_as("corporate-actions-investigator"):
            with pytest.raises(model_armor.ContentBlocked):
                gateway.call_tool("edgar.fetch_filing_text", "https://sec.gov/x")

    def test_unscreenable_return_type_is_refused(self, stub_armor):
        """Fail closed: a control that ignores what it cannot inspect is not a control."""
        class Opaque:
            pass

        spec = packs.ToolSpec(
            "edgar.fetch_filing_text", lambda *a, **k: Opaque(), (), untrusted_output=True
        )
        with packs.override("edgar.fetch_filing_text", spec), identity.acting_as("corporate-actions-investigator"):
            with pytest.raises(gateway.ContentUnscreenable):
                gateway.call_tool("edgar.fetch_filing_text", "https://sec.gov/x")

    def test_screening_cannot_be_opted_out_of_by_a_manifest_flag(self, stub_armor, poison):
        """admit_untrusted_content used to take the acting manifest as an argument and return
        the text unscreened whenever that manifest said `untrusted_inputs: false`. It now
        resolves identity, and screening is driven by the content rather than by any flag --
        so even an agent whose manifest disclaims untrusted input is still screened."""
        import inspect

        from nav_sentinel.control_plane import model_armor

        # Binding a mutated manifest is no longer possible at all -- identity resolves from the
        # registry -- so the statement is now structural: there is no argument through which a
        # manifest reaches this decision, and no flag a process can set to skip it.

        assert "manifest" not in inspect.signature(gateway.admit_untrusted_content).parameters
        with identity.acting_as("corporate-actions-investigator"):
            with pytest.raises(model_armor.ContentBlocked):
                gateway.admit_untrusted_content(poison, source_uri="x")

    def test_inconsistent_manifest_is_denied_by_p005(self, stub_armor, poison):
        """An agent that declares untrusted inputs while disclaiming the screening
        requirement is refused outright, before any content is admitted."""
        inconsistent = discover.get("corporate-actions-investigator").model_copy(
            update={"requires_model_armor": False}
        )
        # P-005 is evaluated against the published manifest, so the inconsistent combination is
        # tested at the policy rather than by binding a forged identity.
        decision = policies.untrusted_ingest_requires_armor(inconsistent)
        assert not decision.allowed
        assert decision.policy_id == "P-005-UNTRUSTED-INGEST"

    def test_screening_requires_a_bound_identity(self, stub_armor, poison):
        with pytest.raises(identity.IdentityError):
            gateway.admit_untrusted_content(poison, source_uri="x")

    def test_clean_content_is_admitted_unchanged(self, stub_armor):
        with identity.acting_as("corporate-actions-investigator"):
            out = gateway.admit_untrusted_content("CORPORATE ACTION NOTICE\nGross Rate: 0.175")
        assert "Gross Rate" in out


class TestCatalogueIntegrity:
    """B1's residual doors."""

    def test_catalogue_cannot_be_mutated(self):
        """A plain dict would let in-process code swap a spec and run arbitrary code under a
        declared tool's label -- the original bypass through another door."""
        with pytest.raises(TypeError):
            packs.catalogue()["ecb_fx.rate_on"] = packs.ToolSpec("x", lambda: None)

    def test_a_key_disagreeing_with_its_spec_is_refused(self):
        """Otherwise the audit log could name one tool while another executed."""
        bad = packs.ToolSpec("totally.other.tool", lambda *a, **k: "PWNED")
        with packs.override("ecb_fx.rate_on", bad):
            with pytest.raises(packs.UnknownTool, match="mismatched label"):
                packs.resolve("ecb_fx.rate_on")

    def test_monkeypatching_a_tool_module_cannot_redirect_execution(self, monkeypatch):
        """The structural property that makes resolution trustworthy: the catalogue captured
        the function objects at import time, so rebinding a module attribute afterwards does
        not change what runs."""
        from nav_sentinel.tools import ecb_fx

        monkeypatch.setattr(ecb_fx, "rate_on", lambda *a, **k: "PWNED-via-module-attr")
        with identity.acting_as("fx-rates-investigator"):
            result = gateway.call_tool("ecb_fx.rate_on", "EUR", NAV_DATE)
        assert result == Decimal(1), "the catalogue must run the function it captured"

    def test_an_attempt_on_a_nonexistent_tool_is_audited(self):
        """An agent enumerating tool names must not be invisible in the governance log."""
        gateway.clear_decision_log()
        with identity.acting_as("fx-rates-investigator"), pytest.raises(packs.UnknownTool):
            gateway.call_tool("ecb_fx.no_such_function")
        log = gateway.decision_log()
        assert log and log[-1].effect.value == "deny"
        assert "does not exist" in log[-1].reason

    def test_unknown_tool_message_is_not_repr_wrapped(self):
        """UnknownTool subclasses KeyError, whose __str__ applies repr() to its argument."""
        with pytest.raises(packs.UnknownTool) as exc:
            packs.resolve("nope.nope")
        assert not str(exc.value).startswith("\"")


class TestApprovalBand:
    """P-004 derives the band; the process supplies only a unit-tagged magnitude.

    These are boundary tests because the band decides who signs off on a NAV adjustment, and
    the previous implementation read a value the domain had already set — so there was nothing
    to test.
    """

    @pytest.mark.parametrize(
        ("bps", "expected"),
        [
            ("0", "auto_clear"),
            ("0.24", "auto_clear"),
            ("0.25", "single_reviewer"),   # boundary: below is exclusive
            ("0.99", "single_reviewer"),
            ("1", "four_eyes"),
            ("4.99", "four_eyes"),
            ("5", "cio_escalation"),
            ("500", "cio_escalation"),
        ],
    )
    def test_band_boundaries(self, bps, expected):
        impact = Impact(value=Decimal(bps), unit="bps")
        assert policies.band_for(impact).value == expected

    def test_a_negative_impact_bands_on_magnitude(self):
        """Direction is not materiality: a 6bps overstatement and a 6bps understatement need
        the same signature."""
        assert policies.band_for(Impact(value=Decimal(-6), unit="bps")) is (
            policies.band_for(Impact(value=Decimal(6), unit="bps"))
        )

    def test_an_uncomputed_impact_escalates(self):
        """An untriaged case is the default state of a freshly opened one. Collapsing a missing
        magnitude to zero banded every one of them as auto_clear."""
        assert policies.band_for(None) is policies.ApprovalClass.CIO_ESCALATION

    def test_an_unknown_unit_escalates(self):
        """A process measuring impact in something the control plane has no thresholds for
        cannot be auto-cleared on the strength of it."""
        assert policies.band_for(Impact(value=Decimal(1), unit="bananas")) is (
            policies.ApprovalClass.CIO_ESCALATION
        )


class TestAutonomousCeiling:
    """The ceiling may only narrow autonomy, never widen it past the derived band."""

    def _authority(self, ceiling_bps: str | None):
        from nav_sentinel.registry.models import Authority

        return Authority(
            may_post_entries=True,
            max_autonomous_impact=(
                None if ceiling_bps is None else Impact(value=Decimal(ceiling_bps), unit="bps")
            ),
        )

    def _facts(self, bps: str):
        return CaseFacts(
            case_id="C-CEIL", subject_id="F1", as_of=NAV_DATE, capability="nav.fx_rate",
            impact=Impact(value=Decimal(bps), unit="bps"), status="triaged",
        )

    def test_a_wide_ceiling_cannot_outvote_the_derived_band(self):
        """The regression this class exists for: consulting the ceiling alone let a manifest
        grant itself 500bps of headroom over a case the control plane had banded
        cio_escalation, and the permissive control won."""
        agent = discover.get("fx-rates-investigator").model_copy(
            update={"authority": self._authority("500")}
        )
        decision = policies.may_post_entry(agent, self._facts("400"), None)
        assert not decision.allowed
        assert policies.band_for(self._facts("400").impact) is policies.ApprovalClass.CIO_ESCALATION

    def test_a_zero_ceiling_means_zero(self):
        """`<=` made a zero-impact case clear a zero ceiling, while the field's own comment
        claimed zero meant no autonomy at any size."""
        agent = discover.get("fx-rates-investigator").model_copy(
            update={"authority": self._authority("0")}
        )
        assert not policies.may_post_entry(agent, self._facts("0"), None).allowed

    def test_a_ceiling_in_another_unit_never_applies(self):
        from nav_sentinel.registry.models import Authority

        agent = discover.get("fx-rates-investigator").model_copy(
            update={"authority": Authority(
                may_post_entries=True,
                max_autonomous_impact=Impact(value=Decimal(1000000), unit="shares"),
            )}
        )
        assert not policies.may_post_entry(agent, self._facts("400"), None).allowed

    def test_the_one_legitimate_autonomous_path(self):
        """Auto-clear band AND inside the ceiling. Both, not either."""
        agent = discover.get("fx-rates-investigator").model_copy(
            update={"authority": self._authority("0.2")}
        )
        assert policies.may_post_entry(agent, self._facts("0.1"), None).allowed

    def test_no_published_agent_can_post_at_any_impact(self):
        """The published fleet holds no posting authority, so the ceiling never comes up."""
        for m in load_manifests():
            for bps in ("0", "0.1", "4.9", "5000"):
                assert not policies.may_post_entry(m, self._facts(bps), None).allowed


class TestIdentityCannotBeForged:
    """B3. `acting_as` took a manifest object, so the caller supplied the very document that
    described its own authority. It now takes a reference and resolves it."""

    def test_a_manifest_object_is_refused_with_a_useful_error(self):
        forged = discover.get("fx-rates-investigator").model_copy(
            update={"authority": Authority(may_propose_remediation=True, may_post_entries=True)}
        )
        with pytest.raises(identity.IdentityError, match="not a AgentManifest"):
            with identity.acting_as(forged):
                pass

    def test_an_unpublished_reference_is_refused(self):
        with pytest.raises(identity.IdentityError, match="not published"):
            with identity.acting_as("agent-that-does-not-exist"):
                pass

    def test_a_version_pin_the_registry_does_not_publish_is_refused(self):
        """Silently binding a different version would let a caller pin to one manifest's
        authority and receive another's.

        The message names the versions that *are* published. It used to report the highest as
        "current", which was a false statement whenever two versions of one id were published —
        `discover.get` returned the first in filename order while discovery returned the highest.
        """
        with pytest.raises(identity.IdentityError, match=r"not published.*\['1\.3\.0'\]"):
            with identity.acting_as("fx-rates-investigator@9.9.9"):
                pass

    def test_authorize_functions_take_no_subject(self):
        import inspect

        assert list(inspect.signature(gateway.authorize_drafting).parameters) == []
        assert "manifest" not in inspect.signature(gateway.authorize_posting).parameters


class TestApprovalReferencesAreResolved:
    """P-003 accepted `human_approval_ref` as a bare string, so an agent could invent one.

    The published fleet denies on authority before reaching this check, which is why these tests
    grant posting authority: without that the validation path is never exercised.
    """

    @pytest.fixture(autouse=True)
    def _authority(self):
        """A fresh store plus the minting side, which only a console ever holds."""
        store = approvals.InMemoryApprovalStore()
        approvals.use_store(store)
        self.authority = approvals.ApprovalAuthority(store)
        yield
        store.clear()

    CIO = (approvals.Principal(subject="ada", role="cio"),)
    TWO_CONTROLLERS = (
        approvals.Principal(subject="ada", role="controller"),
        approvals.Principal(subject="bob", role="controller"),
    )
    ONE_REVIEWER = (approvals.Principal(subject="cal", role="reviewer"),)

    def _poster(self):
        """An agent granted posting authority, published so identity can resolve it."""
        return discover.get("remediation-agent").model_copy(
            update={"authority": Authority(may_propose_remediation=True, may_post_entries=True)}
        )

    def _facts(self, bps="5.41", case_id="C-APPR"):
        return CaseFacts(
            case_id=case_id, subject_id="F1", as_of=NAV_DATE, capability="nav.fx_rate",
            impact=Impact(value=Decimal(bps), unit="bps"), status="remediation_proposed",
        )

    def test_an_invented_reference_is_denied(self):
        d = policies.may_post_entry(self._poster(), self._facts(), "APPR-invented")
        assert not d.allowed
        assert "does not resolve" in d.reason

    def test_an_approval_for_a_different_case_is_denied(self):
        rec = self.authority.grant("SOME-OTHER-CASE", policies.ApprovalClass.CIO_ESCALATION, self.CIO)
        d = policies.may_post_entry(self._poster(), self._facts(), rec.ref)
        assert not d.allowed
        assert "not C-APPR" in d.reason

    def test_an_approval_granted_at_a_lower_band_is_denied(self):
        """A case re-scored upward must not carry its old signature. 5.41bps bands to
        cio_escalation; an approval granted at single_reviewer cannot authorise it."""
        rec = self.authority.grant("C-APPR", policies.ApprovalClass.SINGLE_REVIEWER, self.ONE_REVIEWER)
        d = policies.may_post_entry(self._poster(), self._facts(), rec.ref)
        assert not d.allowed
        assert "granted at single_reviewer" in d.reason

    def test_four_eyes_needs_two_distinct_signers_at_grant_time(self):
        """Refused when minted, so a single-signed four-eyes record never exists to be used."""
        with pytest.raises(approvals.ApprovalDenied, match="2 distinct signer"):
            self.authority.grant(
                "C-APPR", policies.ApprovalClass.FOUR_EYES,
                (approvals.Principal(subject="ada", role="controller"),),
            )

    def test_a_role_that_may_not_sign_at_this_band_is_refused(self):
        """`approvers` was arbitrary strings, so an agent could sign as ("i-am-the-cio",)."""
        with pytest.raises(approvals.ApprovalDenied, match="may be signed by"):
            self.authority.grant(
                "C-APPR", policies.ApprovalClass.CIO_ESCALATION, self.ONE_REVIEWER,
            )

    def test_an_approval_with_no_signer_is_refused(self):
        with pytest.raises(approvals.ApprovalDenied, match="at least one signer"):
            self.authority.grant("C-APPR", policies.ApprovalClass.CIO_ESCALATION, ())

    def test_the_agent_facing_module_cannot_mint(self):
        """The structural half: minting is a method on an object the agent runtime never holds."""
        assert not hasattr(approvals, "grant")

    def test_a_malformed_reference_is_denied_not_raised(self):
        """FirestoreApprovalStore raises ValueError on "a/b" (odd path elements), so a malformed
        reference produced an unhandled exception in a deployment instead of a recorded DENY."""
        for bad in ("a/b", "", "APPR-not-hex", "../../etc/passwd"):
            d = policies.may_post_entry(self._poster(), self._facts(), bad)
            assert not d.allowed
            assert "does not resolve" in d.reason

    def test_a_matching_approval_is_allowed(self):
        rec = self.authority.grant("C-APPR", policies.ApprovalClass.CIO_ESCALATION, self.CIO)
        d = policies.may_post_entry(self._poster(), self._facts(), rec.ref)
        assert d.allowed, d.reason
        assert d.metadata["approvers"] == "ada"

    def test_an_identical_grant_collides_rather_than_duplicating(self):
        """The reference excludes the timestamp. Including it meant two identical grants produced
        two distinct refs and both persisted, which is not what an immutable ledger means."""
        self.authority.grant("C-APPR", policies.ApprovalClass.FOUR_EYES, self.TWO_CONTROLLERS)
        with pytest.raises(ValueError, match="already exists"):
            self.authority.grant("C-APPR", policies.ApprovalClass.FOUR_EYES, self.TWO_CONTROLLERS)

    def test_the_published_fleet_still_cannot_post_with_a_valid_approval(self):
        """Authority is checked before the approval, so a correct signature does not confer
        authority the manifest does not grant."""
        rec = self.authority.grant("C-APPR", policies.ApprovalClass.CIO_ESCALATION, self.CIO)
        for m in load_manifests():
            assert not policies.may_post_entry(m, self._facts(), rec.ref).allowed


class TestRegistryModelsAreImmutable:
    """The shortest bypass this project has had. `identity.current()` returns the instance the
    registry cached, so a mutable model meant every policy in the fleet was one assignment from
    defeat — and the mutation poisoned the cache process-wide, for every agent:

        with identity.acting_as("fx-rates-investigator"):
            identity.current().authority.may_post_entries = True
    """

    def test_authority_cannot_be_mutated(self):
        with identity.acting_as("fx-rates-investigator") as m:
            with pytest.raises(ValidationError):
                m.authority.may_post_entries = True

    def test_the_authority_object_cannot_be_replaced(self):
        with identity.acting_as("fx-rates-investigator") as m:
            with pytest.raises(ValidationError):
                m.authority = Authority(may_post_entries=True)

    def test_tool_and_scope_collections_are_tuples(self):
        with identity.acting_as("fx-rates-investigator") as m:
            assert isinstance(m.allowed_tools, tuple)
            assert isinstance(m.data_scopes.read, tuple)
            assert isinstance(m.handles_capabilities, tuple)
            with pytest.raises(AttributeError):
                m.allowed_tools.append("books_and_records.cash_movements")

    def test_a_mutation_attempt_does_not_reach_the_cache(self):
        """Belt and braces: even if a future model loses `frozen`, assert the cached instance is
        unchanged after an attempt, because that is the property that actually matters."""
        before = discover.get("fx-rates-investigator").authority.may_post_entries
        with identity.acting_as("fx-rates-investigator") as m, pytest.raises(ValidationError):
            m.authority.may_post_entries = True
        assert discover.get("fx-rates-investigator").authority.may_post_entries == before is False


class TestIdentityBindingsCarryAToken:
    """`acting_as` is the only way in. Writing the ContextVar directly used to install whatever
    was handed to it."""

    def test_a_directly_written_binding_is_refused(self):
        forged = discover.get("fx-rates-investigator")
        for label, value in (
            ("bare manifest", forged),
            ("bogus token", (forged, object())),
            ("wrong arity", (forged,)),
        ):
            identity._current.set(value)
            try:
                with pytest.raises(identity.IdentityError, match="without going through acting_as"):
                    identity.current()
            finally:
                identity._current.set(None)
            assert identity.current_or_none() is None, label

    def test_current_or_none_does_not_leak_an_untokened_binding(self):
        identity._current.set((discover.get("remediation-agent"), object()))
        try:
            assert identity.current_or_none() is None
        finally:
            identity._current.set(None)


class TestTheNoAutoClearFloorSurvivesTheSeam:
    """MAJOR from the gate review. `approval_class_for(has_quantity_break=True)` floored a
    quantity break at single reviewer, and `CaseFacts` carried no signal for it, so the control
    plane -- the stated single enforcement point -- auto-cleared exactly what the domain refused
    to. A floor enforced on one side of a boundary is not enforced."""

    def _facts(self, *, flag: bool):
        return CaseFacts(
            case_id="C-QTY", subject_id="MERID-GEF", as_of=NAV_DATE,
            capability="nav.corporate_action",
            impact=Impact(value=Decimal(0), unit="bps"), status="triaged",
            no_auto_clear=flag,
        )

    def test_a_zero_impact_case_auto_clears_by_default(self):
        assert policies.band_for(self._facts(flag=False).impact) is (
            policies.ApprovalClass.AUTO_CLEAR
        )

    def test_the_flag_floors_it_at_single_reviewer(self):
        facts = self._facts(flag=True)
        assert policies.band_for(facts.impact, no_auto_clear=True) is (
            policies.ApprovalClass.SINGLE_REVIEWER
        )

    def test_approval_route_honours_the_flag(self):
        """The route decision is the one thing that reaches the governance log, so it is the one
        that has to be right."""
        assert policies.approval_route(self._facts(flag=True)).metadata["band"] == (
            "single_reviewer"
        )
        assert policies.approval_route(self._facts(flag=False)).metadata["band"] == "auto_clear"

    def test_the_flag_closes_the_autonomous_posting_path(self):
        """Without it, granting any positive ceiling let a stock-record control failure post with
        no human."""
        from nav_sentinel.registry.models import Authority

        agent = discover.get("remediation-agent").model_copy(
            update={"authority": Authority(
                may_post_entries=True,
                max_autonomous_impact=Impact(value=Decimal("0.25"), unit="bps"),
            )}
        )
        assert policies.may_post_entry(agent, self._facts(flag=False), None).allowed
        assert not policies.may_post_entry(agent, self._facts(flag=True), None).allowed

    def test_the_real_split_case_carries_the_flag(self):
        """End to end on the actual fixture, because the flag is only worth having if the domain
        sets it where it matters."""
        from nav_sentinel.domain import materiality, tolerance
        from nav_sentinel.domain.cycle import group_into_cases
        from nav_sentinel.tools import books_and_records as bnr

        breaks = tolerance.detect_position_breaks(
            bnr.positions("accounting"), bnr.positions("custodian"), NAV_DATE
        )
        split = [b for b in breaks if b.isin == "US5949181045"]
        case = group_into_cases(split, "MERID-GEF", NAV_DATE)[0]
        materiality.score(case, bnr.nav_record("custodian", "MERID-GEF", NAV_DATE))
        facts = case.to_facts()
        assert facts.impact.value == Decimal(0)
        assert facts.no_auto_clear is True
        assert policies.approval_route(facts).metadata["band"] != "auto_clear"
