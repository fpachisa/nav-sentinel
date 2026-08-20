# Unpublished manifests

Manifests here are **not loaded**. `load_manifests()` globs `*.yaml` in the pack's
`manifest_dir` and does not recurse, so moving a manifest into this directory removes the agent
from the registry — and therefore from routing, from `discover.coverage()`, and from every
`authorize_*` decision — without deleting the work.

They are here on purpose, and the purpose is evidential rather than tidy.

Two of the plan's acceptance criteria require that a break can be **triaged correctly and then
refused for want of an authorised investigator**: the adversarial pricing case is classified as
`nav.pricing`, the registry reports that nothing is authorised to handle it, and the case escalates
to a human. While these two manifests were published, every capability had an investigator, so that
path was unreachable and the test asserting full coverage passed — which read as completeness and
was in fact the absence of the control.

It also makes a point the architecture depends on: resolving an identity from "the published
registry" only raises the bar if **publication is itself a controlled act**. A directory that is
loaded and one that is not is the cheapest honest demonstration of that.

`nav.pricing` and `nav.cash_fees` are consequently declared capabilities of the NAV pack with no
authorised agent. That gap is asserted, not tolerated — see
`tests/test_governance.py::test_declared_capabilities_without_an_investigator_are_exactly_the_known_gaps`.
