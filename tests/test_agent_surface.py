"""The generated tool surface: what an agent can reach, and what it records doing so.

Three properties. Every call goes through the gateway, so P-001 and P-006 evaluate at runtime
rather than at generation time. Every parameter is exposed as a string and coerced here, because
ADK hands the model's raw text straight through. And every call is recorded as an observation, so
a citation can be checked against a fact rather than against a plausible sentence.
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal

import pytest

from nav_sentinel.control_plane import agent_surface, gateway, identity, packs
from nav_sentinel.control_plane.agent_surface import (
    SurfaceInvalid,
    ToolBudgetExhausted,
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
            with pytest.raises(ValueError, match="Expected date, e.g. 2026-08-17"):
                fx["ecb_fx.rate_on"](currency="USD", day="17 August")

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
        assert set(observed) == {"rate", "rate_date"}
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
            rendered = tools["books_and_records.security"](isin="US02319V1035")
        assert isinstance(rendered, dict)
        assert rendered["isin"] == "US02319V1035"
        assert all(not hasattr(v, "model_dump") for v in rendered.values())


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
            with pytest.raises(ToolBudgetExhausted, match="budget"):
                tools["ecb_fx.rate_on"](currency="USD", day="2026-08-14")

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
