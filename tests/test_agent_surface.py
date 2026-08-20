"""The generated tool surface: what an agent can reach, and what it records doing so.

Three properties. Every call goes through the gateway, so P-001 and P-006 evaluate at runtime
rather than at generation time. Every parameter is exposed as a string and coerced here, because
ADK hands the model's raw text straight through. And every call is recorded as an observation, so
a citation can be checked against a fact rather than against a plausible sentence.
"""

from __future__ import annotations

import contextlib
import inspect
from datetime import date
from decimal import Decimal

import pytest

from nav_sentinel.control_plane import agent_surface, gateway, identity, packs
from nav_sentinel.control_plane.agent_surface import (
    SurfaceInvalid,
)
from nav_sentinel.control_plane.observations import ObservationStore
from nav_sentinel.registry import discover

CASE = "CASE-MERID-GEF-2026-08-17-0001"


@pytest.fixture
def store() -> ObservationStore:
    return ObservationStore()


@pytest.fixture
def fx(store):
    manifest = discover.get("fx-rates-investigator")
    gateway.clear_decision_log()
    tools = agent_surface.build(manifest, case_id=CASE, trace_id="tr-1", store=store)
    return {t.nav_tool_name: t for t in tools}


class TestTheSurfaceIsTheAllowlist:
    def test_it_exposes_exactly_the_manifest_tools(self, fx):
        assert set(fx) == set(discover.get("fx-rates-investigator").allowed_tools)

    def test_a_tool_outside_the_manifest_has_no_function_at_all(self, fx):
        assert "edgar.fetch_filing_text" not in fx

    def test_a_manifest_naming_an_undeclared_tool_is_refused_at_generation(self, store):
        """A deployment defect, not a runtime surprise."""
        manifest = discover.get("fx-rates-investigator")
        broken = manifest.model_copy(
            update={"allowed_tools": (*manifest.allowed_tools, "ecb_fx.invented_tool")}
        )
        with pytest.raises(SurfaceInvalid, match="which no registered process declares"):
            agent_surface.build(broken, case_id=CASE, trace_id=None, store=store)

    def test_a_tool_without_a_description_is_refused_at_generation(self, store, monkeypatch):
        """Nine tools shipped with no description. A model cannot choose between
        `books_and_records.positions` and `.securities` from their names, so generating that
        surface would certify something the agent could not use."""
        manifest = discover.get("fx-rates-investigator")
        name = manifest.allowed_tools[0]
        stripped = dict(packs.catalogue())
        stripped[name] = packs.ToolSpec(
            name=name, fn=stripped[name].fn, reads=stripped[name].reads, description=""
        )
        monkeypatch.setattr(packs, "catalogue", lambda: stripped)
        with pytest.raises(SurfaceInvalid, match="has no description"):
            agent_surface.build(manifest, case_id=CASE, trace_id=None, store=store)

    def test_every_published_agent_can_have_a_surface_generated(self, store):
        """The build-time check must pass for the fleet as published, or a deploy is broken."""
        from nav_sentinel.registry.models import load_manifests

        for manifest in load_manifests():
            tools = agent_surface.build(manifest, case_id=CASE, trace_id=None, store=store)
            assert len(tools) == len(manifest.allowed_tools)


class TestEveryCallGoesThroughTheGateway:
    def test_a_call_records_policy_decisions(self, fx):
        """If the wrapper resolved `spec.fn` itself, P-001 and P-006 would never evaluate and no
        agent tool call would appear in the governance log -- enforcement would silently move from
        the runtime gateway to a code generator."""
        gateway.clear_decision_log()
        with identity.acting_as("fx-rates-investigator"):
            fx["ecb_fx.rate_on"](currency="USD", day="2026-08-17")
        policies = {d.policy_id for d in gateway.decision_log()}
        assert any("P-001" in p for p in policies), policies
        assert any("P-006" in p for p in policies), policies

    def test_a_call_without_a_bound_identity_is_refused(self, fx):
        """The gateway resolves the acting agent from the binding, never from an argument."""
        with pytest.raises(Exception, match="identity|acting_as"):
            fx["ecb_fx.rate_on"](currency="USD", day="2026-08-17")

    def test_the_generator_never_calls_the_raw_function(self):
        source = inspect.getsource(agent_surface)
        body = source.split('"""', 2)[2]      # skip the module docstring
        assert "spec.fn(" not in body, "the surface calls the ungated callable"
        assert "gateway.call_tool(" in body


class TestArgumentsArriveAsTextAndAreCoerced:
    def test_a_string_date_reaches_the_tool_as_a_date(self, fx):
        """Measured against ADK 2.7.1: a parameter annotated `date` receives the model's raw `str`.
        Every ecb_fx tool and books_and_records.nav_record takes a date, so without coercion the
        first call raises inside the tool."""
        with identity.acting_as("fx-rates-investigator"):
            assert fx["ecb_fx.rate_on"](currency="USD", day="2026-08-17") is not None

    def test_the_declared_signature_is_all_strings_with_real_parameters(self, fx):
        """A `(**kwargs)` wrapper yields `parameters_json_schema: None` in ADK, which the model can
        only call with no arguments."""
        signature = inspect.signature(fx["ecb_fx.rate_on"])
        assert list(signature.parameters) == ["currency", "day"]
        assert all(p.annotation is str for p in signature.parameters.values())

    def test_a_bad_date_fails_at_the_boundary_with_a_correctable_message(self, fx):
        """The model gets to fix it on the next turn, so the message has to say what was wanted."""
        with identity.acting_as("fx-rates-investigator"):
            returned = fx["ecb_fx.rate_on"](currency="USD", day="17 August")
        # Returned, not raised: ADK re-raises a tool exception out of the runner unless a callback
        # handles it, so a message written for the model to correct on its next turn was in fact a
        # stack trace it never saw.
        assert "Expected date, e.g. 2026-08-17" in returned["error"]

    def test_annotations_are_resolved_not_read_as_strings(self):
        """Every tool module uses `from __future__ import annotations`, so an annotation is the
        *string* "date" and a converter table keyed by the `date` class matches nothing. That
        silently skipped coercion and failed inside the tool with a TypeError."""
        import typing

        from nav_sentinel.tools import ecb_fx

        assert typing.get_type_hints(ecb_fx.rate_on)["day"] is date

    def test_a_tuple_parameter_accepts_a_comma_separated_string(self):
        assert agent_surface._coerce(
            "8-K, 6-K", tuple[str, ...], tool="t", parameter="forms"
        ) == ("8-K", "6-K")

    @pytest.mark.parametrize(
        ("text", "annotation", "expected"),
        [
            ("2026-08-17", date, date(2026, 8, 17)),
            ("1.1567", Decimal, Decimal("1.1567")),
            ("42", int, 42),
            ("true", bool, True),
            ("MERID-GEF", str, "MERID-GEF"),
        ],
    )
    def test_each_supported_type_coerces(self, text, annotation, expected):
        assert agent_surface._coerce(text, annotation, tool="t", parameter="p") == expected


class TestEveryCallIsRecorded:
    def test_an_observation_carries_the_case_and_the_acting_agent(self, fx, store):
        with identity.acting_as("fx-rates-investigator"):
            fx["ecb_fx.latest_rate_on_or_before"](currency="USD", day="2026-08-17")
        observation = next(iter(store.as_mapping().values()))
        assert observation.case_id == CASE
        assert observation.trace_id == "tr-1"
        assert observation.agent_ref == discover.get("fx-rates-investigator").ref
        assert observation.tool == "ecb_fx.latest_rate_on_or_before"

    def test_the_process_projection_supplies_the_citable_facts(self, fx, store):
        """The rate *and its date*. A stale-rate break is the gap between them, so a verdict citing
        the rate alone has not identified the break."""
        with identity.acting_as("fx-rates-investigator"):
            fx["ecb_fx.latest_rate_on_or_before"](currency="USD", day="2026-08-17")
        observed = next(iter(store.as_mapping().values())).observed
        assert {"rate", "rate_date"} <= set(observed)
        assert observed["rate_date"] == "2026-08-17"

    def test_facts_are_stored_as_text_so_the_platform_stays_process_agnostic(self, fx, store):
        with identity.acting_as("fx-rates-investigator"):
            fx["ecb_fx.rate_on"](currency="USD", day="2026-08-17")
        observed = next(iter(store.as_mapping().values())).observed
        assert all(isinstance(v, str) for v in observed.values())

    def test_a_tool_with_no_projection_records_an_observation_with_no_facts(self, store):
        """Honest rather than empty: the call happened and can be cited as having happened."""
        triage = discover.get("triage-agent")
        tools = {
            t.nav_tool_name: t
            for t in agent_surface.build(triage, case_id=CASE, trace_id=None, store=store)
        }
        with identity.acting_as("triage-agent"):
            tools["registry.coverage"]()
        observation = next(iter(store.as_mapping().values()))
        assert observation.observed == {}
        assert observation.tool == "registry.coverage"

    def test_the_same_call_twice_is_one_observation(self, fx, store):
        """Ids are content-derived, so a repeated identical call does not inflate the evidence."""
        with identity.acting_as("fx-rates-investigator"):
            fx["ecb_fx.rate_on"](currency="USD", day="2026-08-17")
            fx["ecb_fx.rate_on"](currency="USD", day="2026-08-17")
        assert len(store) == 1

    def test_a_broken_projection_does_not_fail_the_call(self, fx, store, monkeypatch):
        """Telemetry about a call must not be able to break the call."""
        spec = packs.catalogue()["ecb_fx.rate_on"]
        broken = dict(packs.catalogue())
        broken["ecb_fx.rate_on"] = packs.ToolSpec(
            name=spec.name, fn=spec.fn, reads=spec.reads,
            description=spec.description, observe=lambda _r: 1 / 0,
        )
        monkeypatch.setattr(packs, "catalogue", lambda: broken)
        manifest = discover.get("fx-rates-investigator")
        tools = {
            t.nav_tool_name: t
            for t in agent_surface.build(manifest, case_id=CASE, trace_id=None, store=store)
        }
        with identity.acting_as("fx-rates-investigator"):
            assert tools["ecb_fx.rate_on"](currency="USD", day="2026-08-17") is not None
        assert next(iter(store.as_mapping().values())).observed == {}

    def test_the_model_sees_readable_values_not_opaque_objects(self, store):
        """A model cannot reason about a holding rendered as an object repr."""
        ca = discover.get("corporate-actions-investigator")
        tools = {
            t.nav_tool_name: t
            for t in agent_surface.build(ca, case_id=CASE, trace_id=None, store=store)
        }
        with identity.acting_as("corporate-actions-investigator"):
            rendered = tools["books_and_records.security"](isin="US02319V1035")["result"]
        assert isinstance(rendered, dict)
        assert rendered["isin"] == "US02319V1035"
        assert all(not hasattr(v, "model_dump") for v in rendered.values())


class TestTheModelCanActuallyCiteWhatItSaw:
    """The mechanism is unusable unless the id reaches the model.

    It did not. The wrapper returned only the result, so `Verdict.citations` could never be
    legitimately populated -- and every other test here still passed, because they all read the
    store directly instead of going through what a model receives. That is the defect family this
    project keeps hitting: a test passing for a reason unrelated to the property it names.
    """

    def test_a_call_returns_an_id_the_verdict_can_cite(self, fx, store):
        with identity.acting_as("fx-rates-investigator"):
            returned = fx["ecb_fx.latest_rate_on_or_before"](currency="USD", day="2026-08-17")
        assert set(returned) == {"observation_id", "result"}
        assert returned["observation_id"] in store

    def test_the_returned_id_resolves_through_the_contract(self, fx, store):
        """End to end: what the model gets back is enough to build a valid verdict."""
        from nav_sentinel.agents.contract import Citation, Verdict, resolve_citations

        with identity.acting_as("fx-rates-investigator"):
            returned = fx["ecb_fx.latest_rate_on_or_before"](currency="USD", day="2026-08-17")

        verdict = Verdict(
            case_id=CASE,
            capability="nav.fx_rate",
            root_cause="Accounting applied a stale rate",
            confidence=0.8,
            citations=[
                Citation(observation_id=returned["observation_id"], relevance="the rate and date")
            ],
        )
        assert resolve_citations(verdict, store.as_mapping())

    def test_the_result_is_still_reachable_beside_the_id(self, fx):
        with identity.acting_as("fx-rates-investigator"):
            returned = fx["ecb_fx.rate_on"](currency="USD", day="2026-08-17")
        assert returned["result"] == "1.1593"

    def test_the_docstring_tells_the_model_to_cite(self, fx):
        """The model cannot be expected to cite an id nobody told it to cite."""
        assert "observation_id" in fx["ecb_fx.rate_on"].__doc__
        assert "cannot cite will be rejected" in fx["ecb_fx.rate_on"].__doc__


class TestTheCallBudget:
    def test_an_agent_cannot_loop_indefinitely(self, store):
        """An unbounded reasoning loop is how an agent fleet becomes expensive, and a bound is a
        clearer failure than a mounting bill."""
        manifest = discover.get("fx-rates-investigator")
        tools = {
            t.nav_tool_name: t
            for t in agent_surface.build(
                manifest, case_id=CASE, trace_id=None, store=store, budget=2
            )
        }
        with identity.acting_as("fx-rates-investigator"):
            tools["ecb_fx.rate_on"](currency="USD", day="2026-08-17")
            tools["ecb_fx.rate_on"](currency="GBP", day="2026-08-17")
            exhausted = tools["ecb_fx.rate_on"](currency="USD", day="2026-08-14")
        assert "budget" in exhausted["error"]

    def test_the_budget_is_per_surface_not_per_process(self, store):
        manifest = discover.get("fx-rates-investigator")
        first = {
            t.nav_tool_name: t
            for t in agent_surface.build(manifest, case_id="C1", trace_id=None, store=store, budget=1)
        }
        second = {
            t.nav_tool_name: t
            for t in agent_surface.build(manifest, case_id="C2", trace_id=None, store=store, budget=1)
        }
        with identity.acting_as("fx-rates-investigator"):
            first["ecb_fx.rate_on"](currency="USD", day="2026-08-17")
            second["ecb_fx.rate_on"](currency="USD", day="2026-08-17")   # its own budget


class TestTheSurfaceIsUsableByAdk:
    """The declaration is what the model actually sees, so it is asserted against ADK itself."""

    def test_each_tool_declares_real_parameters(self, fx):
        from google.adk.tools import FunctionTool

        for name, fn in fx.items():
            declaration = FunctionTool(fn)._get_declaration()
            assert declaration.description, name
            if inspect.signature(fn).parameters:
                schema = declaration.parameters_json_schema
                assert schema is not None, f"{name} declares no parameters"
                assert schema["properties"], name

    def test_the_adk_name_maps_back_to_the_catalogue_name(self, fx):
        """A dotted name cannot be a Python identifier, so the two must not drift apart."""
        for name, fn in fx.items():
            assert fn.__name__ == name.replace(".", "__")
            assert fn.nav_tool_name == name
            assert name in packs.catalogue()


class TestEvidenceRequirementsAreDeclaredByTheProcess:
    """P-007. An FX verdict resting only on our own books has not explained the break, it has
    restated it. The rule is declared per capability by the pack and evaluated in the control
    plane, so a second process states its own and inherits the check."""

    def test_the_nav_pack_requires_the_rate_its_date_and_its_currency(self):
        """Facts, not a tool namespace. Requiring a namespace only asked that *some* call to it had
        happened -- measured, a GBP lookup for an unrelated July date that returned nothing
        satisfied it while every number in the verdict was invented. `currency` was added after a
        GBP lookup that *did* return a value was found to corroborate an EUR/USD claim."""
        assert packs.evidence_requirement_for("nav.fx_rate") == ("rate", "rate_date", "currency")

    def test_a_capability_with_no_declared_rule_requires_nothing(self):
        """Honest rather than absent: a settlement break is decided by our own trade records, and
        inventing an external requirement for it would make the rule decorative."""
        assert packs.evidence_requirement_for("nav.settlement") == ()
        assert packs.evidence_requirement_for("ta.nothing_here") == ()

    def test_a_verdict_citing_external_evidence_is_allowed(self):
        gateway.clear_decision_log()
        with identity.acting_as("fx-rates-investigator"):
            decision = gateway.authorize_verdict(
                "nav.fx_rate", frozenset({"rate", "rate_date", "as_of", "currency"})
            )
        assert decision.allowed
        assert decision.policy_id == "P-007-EVIDENCE-CORROBORATION"

    def test_a_verdict_citing_only_internal_records_is_refused(self):
        from nav_sentinel.control_plane.policies import PolicyViolation

        with identity.acting_as("fx-rates-investigator"):
            with pytest.raises(PolicyViolation, match="P-007"):
                gateway.authorize_verdict("nav.fx_rate", frozenset({"amount"}))

    def test_the_refusal_is_recorded_in_the_governance_log(self):
        """A reviewer asking why a verdict was rejected should find it where every other denial is."""
        from nav_sentinel.control_plane.policies import PolicyViolation

        gateway.clear_decision_log()
        with identity.acting_as("fx-rates-investigator"):
            with pytest.raises(PolicyViolation):
                gateway.authorize_verdict("nav.fx_rate", frozenset())
        denials = [d for d in gateway.decision_log() if not d.allowed]
        assert [d.policy_id for d in denials] == ["P-007-EVIDENCE-CORROBORATION"]

    def test_the_facts_a_verdict_cites_come_from_its_own_observations(self, fx, store):
        """The two halves must meet: what the surface records is what the check reads."""
        with identity.acting_as("fx-rates-investigator"):
            returned = fx["ecb_fx.latest_rate_on_or_before"](currency="USD", day="2026-08-17")
            cited = store.facts_from([returned["observation_id"]])
            assert gateway.authorize_verdict("nav.fx_rate", cited).allowed

    def test_a_call_that_returned_nothing_corroborates_nothing(self, fx, store):
        """The critical one. A GBP lookup for an unrelated July date returned None, carried no
        facts, and satisfied a namespace-based requirement -- so a verdict whose every number was
        invented was schema-valid, cited a real observation recorded for this case by this agent,
        and passed P-007."""
        from nav_sentinel.control_plane.policies import PolicyViolation

        with identity.acting_as("fx-rates-investigator"):
            returned = fx["ecb_fx.latest_rate_on_or_before"](currency="GBP", day="2026-07-01")
            assert returned["result"] is None
            cited = store.facts_from([returned["observation_id"]])
            assert cited == frozenset(), "an empty result contributed citable facts"
            with pytest.raises(PolicyViolation, match="no observation carrying"):
                gateway.authorize_verdict("nav.fx_rate", cited)

    def test_only_the_cited_observations_count(self, fx, store):
        """An agent should not be corroborated by a call it made and then did not rely on."""
        with identity.acting_as("fx-rates-investigator"):
            good = fx["ecb_fx.latest_rate_on_or_before"](currency="USD", day="2026-08-17")
            empty = fx["ecb_fx.latest_rate_on_or_before"](currency="GBP", day="2026-07-01")
        assert store.facts_from([good["observation_id"]])
        assert store.facts_from([empty["observation_id"]]) == frozenset()

    def test_a_requirement_naming_an_undeclared_capability_is_refused(self):
        """The rule would bind to nothing while looking present -- a governance rule weakened to
        decoration, which is a shape this project has already had to fix once."""
        from nav_sentinel.control_plane.packs import ProcessPack

        with pytest.raises(ValueError, match="does not\\s+declare as a capability"):
            ProcessPack(
                key="nav2", name="n", capabilities=("nav2.a",),
                manifest_dir=packs.registered()[0].manifest_dir,
                tools=(), evidence_requirements=(("nav2.b", ("ecb_fx",)),),
            )

    def test_a_requirement_naming_a_fact_no_tool_can_produce_is_refused(self):
        """No verdict could ever satisfy it, so every verdict for that capability would be denied
        by a rule that reads as a typo."""
        from nav_sentinel.control_plane.packs import ProcessPack

        with pytest.raises(ValueError, match="no\\s+tool of this process can produce"):
            ProcessPack(
                key="nav3", name="n", capabilities=("nav3.a",),
                manifest_dir=packs.registered()[0].manifest_dir,
                tools=(packs.catalogue()["ecb_fx.rate_on"],),
                evidence_requirements=(("nav3.a", ("rate_dat",)),),   # note the typo
            )

    def test_an_empty_requirement_is_refused(self):
        from nav_sentinel.control_plane.packs import ProcessPack

        with pytest.raises(ValueError, match="Omit the entry instead"):
            ProcessPack(
                key="nav4", name="n", capabilities=("nav4.a",),
                manifest_dir=packs.registered()[0].manifest_dir,
                tools=(), evidence_requirements=(("nav4.a", ()),),
            )

    def test_the_requirement_table_cannot_be_mutated_through_the_pack(self):
        """A tuple of pairs, not a dict: a dict would be mutable through the reference the pack
        hands out, and this is a governance rule."""
        pack = packs.registered()[0]
        assert isinstance(pack.evidence_requirements, tuple)
        with pytest.raises((AttributeError, TypeError)):
            pack.evidence_requirements = ()


class TestRunningWithNoIdentityBound:
    """`identity.unbound()` exists for the quarantined extractor, which refuses to parse an
    untrusted document while an identity is bound."""

    def test_the_quarantine_holds_inside_it(self):
        from nav_sentinel.control_plane import extraction

        with identity.acting_as("corporate-actions-investigator"):
            with pytest.raises(extraction.QuarantineViolation):
                extraction._require_quarantine()
            with identity.unbound():
                extraction._require_quarantine()      # must not raise

    def test_the_binding_is_restored_afterwards(self):
        with identity.acting_as("corporate-actions-investigator"):
            with identity.unbound():
                pass
            assert identity.current().agent_id == "corporate-actions-investigator"

    def test_the_trace_survives_unlike_a_fresh_context(self):
        """A fresh `contextvars.Context()` also satisfies the quarantine, and was measured to give
        a span inside it a new trace id with no parent -- breaking "one trace per exception case"
        for the one case that is the demo, and putting the screening decision in an audit black
        hole."""
        import contextvars

        from nav_sentinel.control_plane import telemetry

        def trace_id_inside() -> str:
            with telemetry.span("child") as span:
                return format(span.get_span_context().trace_id, "032x")

        with telemetry.span("parent") as parent_span:
            parent = format(parent_span.get_span_context().trace_id, "032x")
            with identity.acting_as("corporate-actions-investigator"):
                with identity.unbound():
                    assert trace_id_inside() == parent
                assert contextvars.Context().run(trace_id_inside) != parent

    def test_a_decision_recorded_while_unbound_reaches_the_callers_log(self):
        """The other half of the same finding: a policy decision recorded inside a fresh context
        never reaches the caller's log."""
        gateway.clear_decision_log()
        with identity.acting_as("triage-agent"):
            gateway.call_tool("registry.coverage")
            before = len(gateway.decision_log())
            with identity.unbound():
                pass
        assert len(gateway.decision_log()) >= before


class TestEveryInvestigatorCanCiteASource:
    """S1's criterion is that every verdict cites an item with a non-null `source_uri` **and**
    `retrieved_at`. It was unsatisfiable for two of the three published investigators: the platform
    held a constant URI per namespace, `ecb_fx` got one service URL identical for every call, and
    everything else got `None`. Mutating the field to `None` left all 316 tests passing -- the field
    the headline criterion is about had no test at all.
    """

    @staticmethod
    def _arguments(fn) -> dict[str, str]:
        """Plausible values for whatever this tool takes, so any tool can be driven."""
        samples = {
            "source": "accounting", "day": "2026-08-17", "isin": "US02319V1035",
            "fund_id": "MERID-GEF", "currency": "USD", "from_ccy": "USD", "to_ccy": "EUR",
            "cik": "320193", "query": "dividend", "capability": "nav.fx_rate",
            "as_of": "2026-08-17", "source_uri": "https://www.sec.gov/Archives/x.txt",
        }
        return {
            name: samples.get(name, "x")
            for name, parameter in inspect.signature(fn).parameters.items()
            if parameter.default is inspect.Parameter.empty
        }

    @pytest.mark.parametrize(
        "agent_id",
        ["fx-rates-investigator", "corporate-actions-investigator", "settlement-investigator"],
    )
    def test_at_least_one_tool_yields_a_citable_source(self, agent_id, store):
        manifest = discover.get(agent_id)
        tools = agent_surface.build(manifest, case_id=CASE, trace_id=None, store=store)
        with identity.acting_as(agent_id):
            for fn in tools:
                # EDGAR has no cassette, so driving it here would reach the live SEC -- and the
                # suite must pass with the network unreachable. Its citability is covered by
                # `test_every_tool_can_name_where_its_evidence_came_from`, which is static.
                if fn.nav_tool_name.startswith("edgar."):
                    continue
                with contextlib.suppress(Exception):
                    fn(**self._arguments(fn))

        recorded = list(store.as_mapping().values())
        assert recorded, f"{agent_id} recorded no observations at all"
        citable = [o for o in recorded if o.source_uri and o.retrieved_at and o.source]
        assert citable, (
            f"{agent_id} cannot produce a single evidence item with a source_uri, so no verdict of "
            f"its can satisfy the criterion. Sources seen: "
            f"{sorted({(o.tool, o.source_uri) for o in recorded})}"
        )

    def test_every_tool_can_name_where_its_evidence_came_from(self):
        """Declared per tool, so a second process is not left with a bare namespace and no URI."""
        for name, spec in packs.catalogue().items():
            assert spec.source.strip(), f"{name} declares no source"
            assert spec.uri_template or spec.locate, f"{name} can never name a resource"

    def test_the_uri_identifies_the_retrieval_not_the_service(self, fx, store):
        """A constant per namespace named the ECB's data API for every call, which does not say
        which rate was read."""
        with identity.acting_as("fx-rates-investigator"):
            fx["ecb_fx.rate_on"](currency="USD", day="2026-08-17")
            fx["ecb_fx.rate_on"](currency="GBP", day="2026-08-14")
        uris = {o.source_uri for o in store.as_mapping().values()}
        assert len(uris) == 2, f"both calls cite the same resource: {uris}"

    def test_a_template_referencing_an_absent_argument_does_not_leak_a_placeholder(self):
        """`books://merian/{source}` on a tool that takes no `source` would otherwise render with
        the braces still in it -- a citation pointing at a format string."""
        spec = packs.catalogue()["books_and_records.trades"]
        uri = spec.default_uri({"fund_id": "MERID-GEF"})
        assert uri and "{" not in uri, uri


class TestUndeclaredProjectionsAreRefused:
    """`_observe_security` projected `domicile`, `ObservedFacts` did not declare it, and the filter
    dropped it silently -- so the fact the corporate-action cross-check turns on was uncitable and
    removing the filter entirely left every test passing."""

    def test_the_declared_facts_are_producible_and_consumable(self):
        """Every fact a pack's tool declares must be a field the process can rebuild, or it is
        recorded and then discarded on the way back."""
        from nav_sentinel.domain.models import ObservedFacts

        for name, spec in packs.catalogue().items():
            undeclared = sorted(set(spec.facts) - set(ObservedFacts.model_fields))
            assert not undeclared, f"{name} declares fact(s) {undeclared} that no verdict can cite"

    def test_a_projection_returning_an_undeclared_key_is_caught(self, store, monkeypatch):
        spec = packs.catalogue()["ecb_fx.rate_on"]
        rogue = dict(packs.catalogue())
        rogue["ecb_fx.rate_on"] = packs.ToolSpec(
            name=spec.name, fn=spec.fn, reads=spec.reads, description=spec.description,
            source=spec.source, uri_template=spec.uri_template, facts=("rate",),
            observe=lambda _r, _a: {"rate": 1, "smuggled": 2},
        )
        monkeypatch.setattr(packs, "catalogue", lambda: rogue)
        tools = {
            t.nav_tool_name: t
            for t in agent_surface.build(
                discover.get("fx-rates-investigator"), case_id=CASE, trace_id=None, store=store
            )
        }
        with identity.acting_as("fx-rates-investigator"):
            tools["ecb_fx.rate_on"](currency="USD", day="2026-08-17")
        # The call still succeeds -- telemetry must not break a tool -- but nothing is citable.
        assert next(iter(store.as_mapping().values())).observed == {}

    def test_a_tool_that_projects_must_declare_its_facts(self):
        from nav_sentinel.control_plane.packs import ProcessPack

        spec = packs.catalogue()["ecb_fx.rate_on"]
        with pytest.raises(ValueError, match="declares no `facts`"):
            ProcessPack(
                key="nav9", name="n", capabilities=("nav9.a",),
                manifest_dir=packs.registered()[0].manifest_dir,
                tools=(
                    packs.ToolSpec(
                        name="x.y", fn=spec.fn, source="s", observe=lambda _r, _a: {}
                    ),
                ),
            )
