"""NAV error remediation: the multi-week, multi-department process.

A fund publishes a NAV. Later it turns out to be wrong. What follows is regulated, runs for weeks,
and no single department can finish it:

* **fund accounting** quantifies the misstatement;
* **transfer agency** identifies who dealt at the wrong price, which fund accounting cannot see;
* **the remediation office** decides materiality *against the fund's recent history* -- a fourth
  pricing error in a quarter is not treated as a first -- then routes for approval;
* nobody in the fleet approves anything;
* and the case cannot close until every affected investor's compensation is confirmed, which is
  what makes it genuinely multi-week rather than merely slow.

**This package is a third process, not a supervisor.** Fund accounting and transfer agency stay
isolated and never learn it exists. Their contributions arrive through the gateway, under their own
identities, restricted by their own manifests. A case cannot belong to two processes here -- one
capability string, one pack, one threshold set -- so the coordination goes *through* the platform
rather than around it.

Named `remediation_office` rather than `compliance` because `nav_sentinel/compliance.py` already
exists: it is the qualifying-stack probe behind `make compliance`, and a package of that name could
not coexist with it.
"""
