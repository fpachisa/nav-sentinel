# Unpublished manifests — remediation office

Not loaded. `load_manifests()` globs `*.yaml` in the pack's `manifest_dir` and does not recurse, so
an agent here does not exist as far as the registry is concerned.

`rem.regulator_notification` is a real stage of this process and **nobody in this fleet is
authorised to perform it.** Drafting external correspondence on a regulated entity's behalf is not
authority an agent here holds, and publishing an agent for it would be claiming that authority. So
the capability is declared, the registry reports NONE, and a case reaching that stage escalates to
a human.

Keeping the manifest here rather than deleting it also makes **discovery observable**. Moving one
file up a directory and calling `discover.republish()` is the entire act of publication: a case that
could not route a moment ago routes now, with no other change. A registry whose contents cannot be
watched to change is a lookup table, and "an organization can discover your agents" is a claim about
a thing that changes.
