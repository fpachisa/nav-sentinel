"""Coordination through the gateway: one department asking another, under the other's identity.

The track asks for a sub-agent reached *through* the Agent Gateway rather than imported. The
property that makes that worth anything is negative: **the caller's privileges are not lent.** A
remediation officer that could read the share register by asking transfer agency to read it for
itself would be the same coupling as an import, with an audit record that made it look governed.
"""

from __future__ import annotations

import pytest

from nav_sentinel import composition
from nav_sentinel.control_plane import gateway, identity, packs
from nav_sentinel.control_plane.policies import PolicyViolation


def brief(capability: str = "rem.materiality"):
    """A minimal case brief. `delegate` takes one by signature now, because it has to re-stamp the
    capability before the sub-agent sees it."""
    from datetime import date

    from nav_sentinel.control_plane.governance import CaseBrief

    return CaseBrief(
        case_id="CASE-REM-TEST",
        subject_id="MERID-GEF",
        as_of=date(2026, 8, 17),
        capability=capability,
        breaks="  - published NAV misstated by 30bps",
    )


OFFICER = "remediation-officer"
REPORTER = "dealing-impact-reporter"
IMPACT = "ta.dealing_impact"


@pytest.fixture
def invoked() -> list[tuple[str, tuple, dict]]:
    """Replace the real agent runner with a recorder. Offline, and it lets a test assert what
    identity was bound at the moment the sub-agent ran -- which is the whole point."""
    seen: list[tuple[str, tuple, dict]] = []

    def recorder(manifest, *args, **kwargs):
        seen.append((manifest.agent_id, args, kwargs))
        # The bound identity at this instant is what every downstream P-001 check will read.
        seen.append(("bound", (identity.current().agent_id,), {}))
        return {"holders": 41, "units": "2140000"}

    original = gateway._invoker
    gateway.register_agent_invoker(recorder)
    try:
        yield seen
    finally:
        gateway.register_agent_invoker(original)


class TestTheRequestIsGovernedBeforeItRuns:
    def test_a_declared_delegation_is_allowed_and_recorded(self, invoked):
        gateway.mark_decisions("delegate")
        with identity.acting_as(OFFICER):
            gateway.delegate(IMPACT, brief())

        allowed = [
            d
            for d in gateway.decisions_since("delegate")
            if d.policy_id == "P-009-DELEGATION" and d.effect.value == "allow"
        ]
        assert len(allowed) == 1
        assert allowed[0].agent_ref.startswith(OFFICER)
        assert allowed[0].resource == IMPACT

    def test_an_undeclared_capability_is_refused(self, invoked):
        """The register investigator's own capability. The officer may ask for dealing impact and
        nothing else, and "nothing else" has to include capabilities that plainly exist."""
        with identity.acting_as(OFFICER), pytest.raises(PolicyViolation) as refused:
            gateway.delegate("ta.subscription_in_transit", brief())
        assert "P-009" in str(refused.value)
        assert invoked == [], "the sub-agent ran despite the refusal"

    def test_a_process_that_declares_no_delegations_may_not_delegate_at_all(self, invoked):
        """Transfer agency coordinates with nobody. Its pack declares no delegations, so its agents
        cannot start asking other departments for things."""
        with identity.acting_as("register-investigator"), pytest.raises(PolicyViolation):
            gateway.delegate(IMPACT, brief())
        assert invoked == []

    def test_the_refusal_names_what_the_process_may_ask_for(self, invoked):
        with identity.acting_as(OFFICER), pytest.raises(PolicyViolation) as refused:
            gateway.delegate("nav.fx_rate", brief())
        assert IMPACT in str(refused.value), "a refusal that does not say what is allowed is a wall"

    def test_delegating_with_no_identity_bound_is_refused(self, invoked):
        with identity.unbound(), pytest.raises(identity.IdentityError):
            gateway.delegate(IMPACT, brief())
        assert invoked == []


class TestTheSubAgentRunsAsItself:
    """The security property. Everything else here is plumbing."""

    def test_the_bound_identity_switches_to_the_sub_agent(self, invoked):
        with identity.acting_as(OFFICER):
            gateway.delegate(IMPACT, brief())
        bound = [entry for entry in invoked if entry[0] == "bound"]
        assert bound and bound[0][1][0] == REPORTER, (
            "the sub-agent ran under the caller's identity, so every downstream check read the "
            "caller's allowlist"
        )

    def test_the_callers_identity_is_restored_afterwards(self, invoked):
        with identity.acting_as(OFFICER):
            gateway.delegate(IMPACT, brief())
            assert identity.current().agent_id == OFFICER

    def test_the_caller_cannot_read_what_the_sub_agent_reads(self):
        """The officer holds one tool and it is not the register. Delegation must not widen that."""
        with identity.acting_as(OFFICER), pytest.raises(PolicyViolation):
            gateway.call_tool("register.dealt_on", "MERID-GEF", "2026-08-17")

    def test_the_sub_agent_cannot_read_what_the_caller_reads(self, invoked):
        """And the reverse, which is the half people forget. The reporter has no case-history
        access, so it cannot look up the recurrence count the officer decides against."""
        with identity.acting_as(REPORTER), pytest.raises(PolicyViolation):
            gateway.call_tool("memory.prior_errors", "MERID-GEF", "2026-07-01")

    def test_the_two_allowlists_are_disjoint(self):
        """Asserted structurally, so a future manifest edit that quietly grants the officer a
        register read shows up here rather than in a demo."""
        from nav_sentinel.registry import discover

        officer = set(discover.get(OFFICER).allowed_tools)
        reporter = set(discover.get(REPORTER).allowed_tools)
        assert officer and reporter
        assert officer.isdisjoint(reporter), officer & reporter


class TestDepthIsBounded:
    def test_the_ceiling_is_one_hop(self):
        """Asserted as a literal, deliberately.

        The first version of the test below set the current depth to `MAX_DELEGATION_DEPTH` itself,
        so raising the ceiling raised what the test set and the test could never fail. A constant
        compared against itself proves the constant exists, not that it is enforced.
        """
        assert gateway.MAX_DELEGATION_DEPTH == 1

    def test_a_second_hop_is_refused(self, invoked):
        """An agent that delegates to an agent that delegates is a loop with a model in it."""
        with identity.acting_as(OFFICER):
            # Already one hop deep, as a sub-agent's context would be. A literal, not the constant.
            token = gateway._delegation_depth.set(1)
            try:
                with pytest.raises(PolicyViolation) as refused:
                    gateway.delegate(IMPACT, brief())
            finally:
                gateway._delegation_depth.reset(token)
        assert "depth" in str(refused.value)
        assert invoked == [], "the sub-agent ran at a depth the policy refused"

    def test_depth_is_restored_after_a_delegation(self, invoked):
        before = gateway._delegation_depth.get()
        with identity.acting_as(OFFICER):
            gateway.delegate(IMPACT, brief())
        assert gateway._delegation_depth.get() == before

    def test_depth_is_restored_even_when_the_sub_agent_raises(self):
        def explode(*_args, **_kwargs):
            raise RuntimeError("the sub-agent failed")

        original = gateway._invoker
        gateway.register_agent_invoker(explode)
        try:
            before = gateway._delegation_depth.get()
            with identity.acting_as(OFFICER), pytest.raises(RuntimeError):
                gateway.delegate(IMPACT, brief())
            assert gateway._delegation_depth.get() == before
        finally:
            gateway.register_agent_invoker(original)


class TestUnroutableAndUnwired:
    def test_a_capability_nobody_publishes_is_refused_and_recorded(self, invoked):
        """The discovery beat. `rem.regulator_notification` is declared by this very process and
        published by nobody, so the registry refuses to route rather than improvising."""
        packs.registered()  # ensure configuration has happened
        gateway.mark_decisions("unroutable")
        with identity.acting_as(OFFICER):
            # Permit it for this test only: the point is the *registry* refusing, not P-009.
            rem = packs.process_of("rem.materiality")
            widened = packs.ProcessPack(
                **{
                    **rem.__dict__,
                    "delegations": ("rem.regulator_notification",),
                }
            )
            packs._packs[rem.key] = widened
            try:
                with pytest.raises(gateway.UnroutableCapability):
                    gateway.delegate("rem.regulator_notification", brief())
            finally:
                packs._packs[rem.key] = rem
        denials = [
            d
            for d in gateway.decisions_since("unroutable")
            if d.policy_id == "P-009-DELEGATION" and d.effect.value == "deny"
        ]
        assert denials and "no published agent" in denials[0].reason

    def test_delegation_without_a_registered_invoker_raises(self):
        """Rather than returning nothing. A delegation that silently produced None would look
        exactly like a sub-agent that found nothing, and those mean opposite things."""
        original = gateway._invoker
        gateway.register_agent_invoker(None)
        try:
            gateway._invoker = None
            with identity.acting_as(OFFICER), pytest.raises(gateway.NoInvoker):
                gateway.delegate(IMPACT, brief())
        finally:
            gateway.register_agent_invoker(original)


class TestTheRealInvokerIsWired:
    def test_the_composition_root_registers_one(self):
        """The tests above supply their own. This asserts something actually wires the real thing --
        the hole that made `register-investigator` publishable and unrunnable."""
        composition.configure()
        assert gateway._invoker is not None


class TestTheDelegatedBriefCarriesTheDelegatedCapability:
    """The bug 738 passing tests did not catch.

    Every delegation test above supplies a fake invoker that ignores the brief, so the brief's
    *contents* went unchecked -- and the brief arrives carrying the caller's capability. Handing that
    to the sub-agent breaks two things at once: `investigate` refuses work the sub-agent's manifest
    never claimed, and `evidence_requirement_for` resolves the caller's P-007 rule instead of the
    one for the capability actually being performed. The first live run failed on the first hop.
    """

    def test_the_capability_is_restamped_to_the_requested_one(self, invoked):
        received: list[str] = []

        def capture(_manifest, delegated, **_kwargs):
            received.append(delegated.capability)
            return {}

        gateway.register_agent_invoker(capture)
        with identity.acting_as(OFFICER):
            gateway.delegate(IMPACT, brief("rem.materiality"))
        assert received == [IMPACT], "the sub-agent was handed the caller's capability"

    def test_the_sub_agents_own_manifest_would_have_refused_the_callers_capability(self):
        """Proving the guard that caught this is real, using the actual investigator check."""
        import asyncio

        from nav_sentinel.agents.investigator import NotAuthorisedForCapability, investigate
        from nav_sentinel.registry import discover

        with pytest.raises(NotAuthorisedForCapability):
            asyncio.run(
                investigate(brief("rem.materiality"), discover.get(REPORTER))
            )

    def test_the_evidence_rule_that_applies_is_the_delegated_ones(self):
        """P-007 must hold the sub-agent to *its* requirement, not the caller's."""
        officer_rule = gateway.evidence_requirement_for("rem.materiality")
        reporter_rule = gateway.evidence_requirement_for(IMPACT)
        assert officer_rule and reporter_rule
        assert officer_rule != reporter_rule, "the test cannot distinguish the two rules"
        assert "holders" in reporter_rule
        assert "prior_errors" in officer_rule


class TestObservationsAreReadAsObjectsNotIds:
    """`ObservationStore.__iter__` yields observation *ids*. A loop over the store reads strings,
    so every `.observed` lookup raises -- which is how the walkthrough's holder count was written
    against a type it never received."""

    @staticmethod
    def _store_with(*facts: dict[str, str]):
        from datetime import UTC, datetime

        from nav_sentinel.control_plane.observations import Observation, ObservationStore

        store = ObservationStore()
        for index, observed in enumerate(facts):
            store.record(
                Observation(
                    observation_id=f"OBS-order{index:011d}",
                    case_id="CASE-ORDER",
                    trace_id="d8bc651a64bdcd4eac21517327b02b85",
                    agent_ref="dealing-impact-reporter@1.0.0",
                    tool="register.dealt_on",
                    args=f"trade_date={observed.get('trade_date')}",
                    digest=f"{index:016d}",
                    retrieved_at=datetime(2026, 8, 19, 9, index, tzinfo=UTC),
                    source="share_register",
                    observed=observed,
                    summary="",
                )
            )
        return store

    def test_iterating_the_store_yields_ids_not_observations(self):
        """Asserted against a *populated* store. The earlier version ran on an empty one with an
        `or len(store) == 0` escape, so changing `__iter__` to yield values kept it green."""
        store = self._store_with({"holders": "4", "trade_date": "2026-08-17"})
        assert len(store) == 1
        assert all(isinstance(item, str) for item in store)

    def test_the_population_is_selected_by_date_not_by_insertion_order(self):
        """The blocker this replaces a source-text assertion for.

        The old test asserted the string `"as_mapping()"` appeared in the function's source, which a
        constant satisfies. Measured on the old implementation: an agent that probed 2026-08-13
        first (nobody dealt) and 2026-08-17 second (four holders) yielded **0** -- and zero affected
        investors closes a material NAV error with nothing paid.
        """
        from nav_sentinel import remediation_cli

        store = self._store_with(
            {"holders": "0", "trade_date": "2026-08-13"},
            {"holders": "4", "trade_date": "2026-08-17"},
        )
        assert remediation_cli._holder_count(store, dealing_date="2026-08-17") == 4
        assert remediation_cli._holder_count(store, dealing_date="2026-08-13") == 0

    def test_a_genuine_nil_return_is_not_read_as_missing(self):
        """`if recorded:` treated "0" as absent. Currently masked because observed values are
        strings, so `"0"` is truthy -- one change to the projection type and a real nil return
        silently becomes "I never looked"."""
        from nav_sentinel import remediation_cli

        store = self._store_with({"holders": "0", "trade_date": "2026-08-17"})
        assert remediation_cli._holder_count(store, dealing_date="2026-08-17") == 0

    def test_an_unexamined_date_raises_rather_than_reporting_zero(self):
        """Zero is a real answer -- nobody dealt. "I never looked" must not be reported as it."""
        from nav_sentinel import remediation_cli

        store = self._store_with({"holders": "4", "trade_date": "2026-08-17"})
        with pytest.raises(remediation_cli.ImpactNotEstablished):
            remediation_cli._holder_count(store, dealing_date="2026-08-18")


class TestAManifestCannotWidenItsOwnDelegations:
    """A one-line YAML edit was enough to defeat the whole design.

    `packs.delegations_for` unions the delegations of every pack owning *any* of an agent's declared
    capabilities, and nothing checked that an agent's capabilities belong to its own process. So a
    fund-accounting investigator that also declared `rem.materiality` inherited the remediation
    office's right to request share-register dealing counts -- data P-006 denies it directly, and
    which it would receive because delegation runs the *sub-agent's* allowlist. `delegations` lives
    on the pack precisely so an agent's own document cannot widen it; that was enforced by nothing.
    """

    @staticmethod
    def _greedy():
        from nav_sentinel.registry import discover
        from nav_sentinel.registry.models import AgentManifest

        base = discover.get("fx-rates-investigator").model_dump()
        base.update(version="2.0.0", handles_capabilities=("nav.fx_rate", "rem.materiality"))
        return AgentManifest.model_validate(base)

    def test_publication_refuses_a_manifest_spanning_two_processes(self):
        from nav_sentinel.registry import discover

        with pytest.raises(discover.PublicationRefused) as refused:
            discover.validate_fleet((self._greedy(),))
        assert "more than one process" in str(refused.value)

    def test_the_capability_it_reached_for_is_one_it_would_have_inherited(self):
        """Without this the test could pass against a capability that grants nothing."""
        greedy = self._greedy()
        inherited = packs.delegations_for(greedy.handles_capabilities)
        assert IMPACT in inherited, (
            "the fabricated manifest inherits no delegation, so refusing it proves nothing"
        )
        assert IMPACT not in packs.delegations_for(("nav.fx_rate",))

    def test_every_published_agent_belongs_to_exactly_one_process(self):
        from nav_sentinel.registry import discover

        for manifest in discover.all_agents():
            owners = {
                owner.key
                for capability in manifest.handles_capabilities
                if (owner := packs.process_of(capability)) is not None
            }
            assert len(owners) <= 1, (manifest.ref, sorted(owners))


class TestAnUnroutableRequestRecordsOneDecision:
    """It recorded two: an ALLOW from P-009 followed by a DENY when routing failed. Anyone counting
    allowed delegations got a hit for a delegation that never ran."""

    def test_exactly_one_decision_for_an_unroutable_capability(self, invoked):
        rem = packs.process_of("rem.materiality")
        widened = packs.ProcessPack(
            **{**rem.__dict__, "delegations": ("rem.regulator_notification",)}
        )
        packs._packs[rem.key] = widened
        gateway.mark_decisions("one-decision")
        try:
            with identity.acting_as(OFFICER), pytest.raises(gateway.UnroutableCapability):
                gateway.delegate("rem.regulator_notification", brief())
        finally:
            packs._packs[rem.key] = rem

        recorded = [
            d for d in gateway.decisions_since("one-decision") if d.policy_id == "P-009-DELEGATION"
        ]
        assert len(recorded) == 1, [(d.effect.value, d.reason[:40]) for d in recorded]
        assert recorded[0].effect.value == "deny"

    def test_an_allowed_delegation_still_records_its_allow(self, invoked):
        gateway.mark_decisions("still-allows")
        with identity.acting_as(OFFICER):
            gateway.delegate(IMPACT, brief())
        recorded = [
            d for d in gateway.decisions_since("still-allows") if d.policy_id == "P-009-DELEGATION"
        ]
        assert [d.effect.value for d in recorded] == ["allow"]
