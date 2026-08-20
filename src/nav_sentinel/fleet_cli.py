"""`python -m nav_sentinel.fleet_cli` -- show the published fleet and its coverage.

Outside both layers, beside `composition`, for the same reason it is: an entry point has to call
`configure()`, which imports a process and the platform both. Inside `registry/` it made a platform
package depend on the wiring that composes it -- and the seam test could not see that, because
`from nav_sentinel import composition` recorded only the edge `nav_sentinel`, so every import in
that form was invisible to the whole check.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from nav_sentinel import composition
from nav_sentinel.control_plane import identity
from nav_sentinel.registry import discover


def main() -> None:
    # The registry has no manifests until the processes are registered. Without this the CLI
    # raises rather than printing an empty table -- correct, but only if someone calls it.
    composition.configure()

    console = Console()

    table = Table(title="Agent Registry -- published fleet", header_style="bold")
    table.add_column("Reference")
    table.add_column("Identity")
    table.add_column("Handles")
    table.add_column("Tools", justify="right")
    table.add_column("Armor", justify="center")
    table.add_column("Draft", justify="center")
    table.add_column("Post", justify="center")

    for m in discover.all_agents():
        table.add_row(
            m.ref,
            identity.service_account_email(m).split("@")[0],
            ", ".join(m.handles_capabilities) or "-",
            str(len(m.allowed_tools)),
            "yes" if m.requires_model_armor else "-",
            "yes" if m.authority.may_propose_remediation else "-",
            "[red]yes[/red]" if m.authority.may_post_entries else "no",
        )
    console.print(table)

    cov = Table(title="Coverage by capability", header_style="bold")
    cov.add_column("Category")
    cov.add_column("Authorised investigator")
    for cat, ref in discover.coverage().items():
        cov.add_row(cat, ref or "[red]NONE[/red]")
    console.print(cov)


if __name__ == "__main__":
    main()
