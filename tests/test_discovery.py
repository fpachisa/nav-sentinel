"""How an organisation discovers these agents — the track's first focus area.

Discovery is only a claim about something that *changes*. A registry whose contents cannot be
watched to change is a lookup table, so these tests do the whole act of publication: a capability
resolves to nobody, a manifest moves one directory, the registry is republished, and the same
capability now routes. Nothing else changes.

`rem.regulator_notification` is the right subject for it. Drafting correspondence to a regulator on
a fund's behalf is not authority this fleet holds, so it is declared, published by nobody, and
escalates to a human — which is the honest reason for the gap rather than a convenient one.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from nav_sentinel import composition
from nav_sentinel.control_plane import packs
from nav_sentinel.registry import discover

CAPABILITY = "rem.regulator_notification"
AGENT = "regulator-notifier"


@pytest.fixture
def manifests() -> Path:
    return Path(packs.process_of(CAPABILITY).manifest_dir)


@pytest.fixture
def publish(manifests):
    """Move the unpublished manifest into the loaded directory, then put it back.

    A real file move and a real `republish()`, because the point is that publication is an act with
    an observable effect. Faking it with a monkeypatched registry would demonstrate the mock.
    """
    source = manifests / "unpublished" / f"{AGENT}.yaml"
    target = manifests / f"{AGENT}.yaml"
    assert source.is_file(), source

    def _publish() -> None:
        shutil.copy2(source, target)
        discover.republish()

    try:
        yield _publish
    finally:
        target.unlink(missing_ok=True)
        discover.republish()
        composition.configure()


class TestAnUnpublishedCapabilityIsRefusedNotImprovised:
    def test_the_capability_is_declared(self):
        """The gap has to be a *declared* capability, or the registry is silent rather than
        refusing — and silence is what a missing feature looks like."""
        assert CAPABILITY in packs.capabilities()

    def test_nothing_routes_to_it(self):
        assert discover.discover_for_capability(CAPABILITY) is None

    def test_coverage_reports_it_as_a_gap(self):
        assert discover.coverage()[CAPABILITY] is None

    def test_the_agent_is_not_in_the_published_fleet(self):
        assert AGENT not in {m.agent_id for m in discover.all_agents()}

    def test_asking_for_it_by_id_fails(self):
        with pytest.raises(KeyError):
            discover.get(AGENT)


class TestPublishingIsOneObservableAct:
    def test_the_same_capability_routes_once_the_manifest_is_published(self, publish):
        assert discover.discover_for_capability(CAPABILITY) is None
        publish()
        routed = discover.discover_for_capability(CAPABILITY)
        assert routed is not None
        assert routed.agent_id == AGENT

    def test_coverage_closes_the_gap(self, publish):
        before = {c for c, ref in discover.coverage().items() if ref is None}
        publish()
        after = {c for c, ref in discover.coverage().items() if ref is None}
        assert before - after == {CAPABILITY}, "publication changed something else too"

    def test_the_newly_published_agent_passes_the_same_fleet_invariants(self, publish):
        """Publication is not a bypass. A manifest arriving this way is validated exactly as the
        others are, so it cannot grant itself posting authority on the way in."""
        publish()
        manifest = discover.get(AGENT)
        discover.validate_fleet((manifest,))
        assert manifest.authority.may_post_entries is False
        assert manifest.authority.may_propose_remediation is False

    def test_unpublishing_removes_it_again(self, publish):
        publish()
        assert discover.discover_for_capability(CAPABILITY) is not None
        (Path(packs.process_of(CAPABILITY).manifest_dir) / f"{AGENT}.yaml").unlink()
        discover.republish()
        assert discover.discover_for_capability(CAPABILITY) is None


class TestTheRegistryIsTheOnlyRouter:
    def test_every_routable_capability_names_the_process_that_owns_it(self):
        """Discovery is by capability, and a capability is namespaced to its process — so an
        organisation reading the registry can see which department owns what."""
        for capability in packs.capabilities():
            owner = packs.process_of(capability)
            assert owner is not None, capability
            assert capability.startswith(f"{owner.key}."), capability

    def test_coverage_lists_every_declared_capability(self):
        """Including the gaps. A coverage report that omitted what nobody handles would read as
        completeness, which is the failure the unpublished directory exists to prevent."""
        assert set(discover.coverage()) == set(packs.capabilities())
