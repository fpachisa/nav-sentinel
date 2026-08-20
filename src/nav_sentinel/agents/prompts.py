"""Prompt templates, loaded from files rather than embedded in Python.

The instruction is the part of an agent that changes most and reads worst inside a function. Three
prompt defects in this project were found by measurement -- a missing base currency, a missing
citation demand, a self-contradictory currency label -- and each meant editing an f-string threaded
through assembly logic. A reviewer who wants to check what an agent was told should be able to read
one file.

**`string.Template`, not `str.format`.** Prompts contain JSON: the investigator's tells the model
that a tool returns `{"observation_id": ..., "result": ...}`. With `str.format` every one of those
braces needs doubling, which is exactly the kind of invisible escaping that makes a template file
worse than the f-string it replaced. `$name` substitution leaves braces alone.

Templates are found through the **process pack**, like manifests, so a second process ships its own
instructions without touching this module -- reached via the gateway, because the agents layer may
not import `packs` and the seam test says so.
"""

from __future__ import annotations

from string import Template
from typing import TYPE_CHECKING

from nav_sentinel.control_plane import gateway

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

SUFFIX = ".md"


class PromptMissing(FileNotFoundError):
    """No registered process ships a prompt for that agent."""


class PromptIncomplete(KeyError):
    """The template names a placeholder the caller did not supply.

    Raised rather than left blank. A prompt silently missing its evidence block is the failure that
    took triage from 7-of-7 to 2-of-6, and it would read as a model regression rather than a
    template one.
    """


def path_for(agent_id: str) -> Path:
    """Locate an agent's template across every registered process."""
    for directory in gateway.prompt_dirs():
        candidate = directory / f"{agent_id}{SUFFIX}"
        if candidate.is_file():
            return candidate
    searched = [str(d) for d in gateway.prompt_dirs()] or ["<no process registered>"]
    raise PromptMissing(
        f"no prompt template for {agent_id!r}. Searched: {searched}. Add "
        f"{agent_id}{SUFFIX} to the process's prompt directory."
    )


def load(agent_id: str) -> str:
    return path_for(agent_id).read_text()


def render(agent_id: str, **context: object) -> str:
    """Fill an agent's template, refusing to leave a placeholder unsubstituted."""
    template = Template(load(agent_id))
    try:
        return template.substitute(
            {key: "" if value is None else value for key, value in context.items()}
        ).strip() + "\n"
    except KeyError as exc:
        raise PromptIncomplete(
            f"{agent_id}{SUFFIX} names ${exc.args[0]}, which was not supplied. Given: "
            f"{sorted(context)}"
        ) from exc


def placeholders(agent_id: str) -> frozenset[str]:
    """Which names a template expects. Used by a test to keep templates and callers in step."""
    import re

    return frozenset(re.findall(r"\$\{?([a-z_]+)\}?", load(agent_id)))
