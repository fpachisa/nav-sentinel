"""Transfer agency: the share register, as a second process on the same control plane.

This package exists to make one claim checkable rather than arguable. Adding a process should touch
no platform code, and `git diff --stat` for this section is the evidence: new files here, one line
in the composition root, one entry in the seam test's package list. Nothing under `control_plane/`
or `registry/`.

It is a different shape of problem on purpose:

* **Its control total is in shares, not currency.** Fund accounting reconciles money; a share
  register reconciles units. The control plane derives the approval band from a *unit-tagged
  magnitude* and never from an amount it assumes is cash, and a process measuring in `shares`
  exercises that where a second money process would not.
* **One of its corrections needs no model at all.** A subscription in transit is corrected by adding
  the in-transit units -- arithmetic, and the fleet says so rather than putting a language model on
  a step that does not need judgement.
* **It declares a capability nobody is authorised to handle**, so the registry reports NONE instead
  of routing it somewhere plausible.
"""
