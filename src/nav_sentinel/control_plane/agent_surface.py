"""Build the tool surface one agent may use on one case.

Three properties, none of which survive being left implicit.

**It lives in the control plane, not in `agents/`.** Generating the surface means reading the pack
catalogue, and `packs.resolve(name).fn` is the live, ungated callable -- `MappingProxyType` stops a
spec being replaced, not read. Had the generator sat under `agents/`, one line in that module would
have bypassed the gateway entirely while passing a seam test that only forbids importing
`nav_sentinel.tools.*`. `agents/` receives a finished list of tools and may import neither `packs`
nor `domain.pack`.

**Every wrapper calls `gateway.call_tool`.** If it resolved `.fn` itself, P-001 and P-006 would
never evaluate and no agent tool call would appear in the governance log: allowlist enforcement
would move from the runtime gateway to a code generator, at build time, silently.

**Parameters are strings, coerced here.** Measured against ADK 2.7.1: a variadic wrapper yields
`parameters_json_schema: None`, so the model would call every tool with no arguments; and a
parameter annotated `date` receives the model's raw `str` uncoerced, so every `ecb_fx` tool and
`books_and_records.nav_record` would raise on first call. Coercion is explicit and its failures are
readable, because a coercion error is something the model can correct on its next turn.

The surface is generated **per case**, with `case_id`, `trace_id` and `agent_ref` captured in each
closure. There is no bound-case ContextVar to read -- `src/` has exactly two, for identity and the
decision log -- and a closure cannot leak a case into another agent's call.
"""

from __future__ import annotations

import logging
import typing
from collections.abc import Callable  # noqa: TC003 -- used in a runtime annotation
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from nav_sentinel.control_plane import gateway, packs
from nav_sentinel.control_plane.observations import (
    Observation,
    ObservationStore,
    digest_of,
    observation_id,
    stringify,
    utcnow,
)

if TYPE_CHECKING:  # pragma: no cover
    from nav_sentinel.registry.models import AgentManifest

logger = logging.getLogger(__name__)

#: How many times one agent may call tools while investigating one case. An unbounded reasoning
#: loop is the predictable way an agent fleet becomes expensive, and a bound is also a far clearer
#: failure than a slowly mounting bill.
DEFAULT_CALL_BUDGET = 12


class ToolBudgetExhausted(RuntimeError):
    """The agent used its whole tool-call budget on one case."""


class SurfaceInvalid(RuntimeError):
    """A manifest and the pack catalogue disagree, or a tool is undocumented.

    Raised at generation time. A manifest naming a tool nobody declares is a deployment defect,
    and a tool with no description declares a surface the model cannot choose from.
    """


# --------------------------------------------------------------------------- coercion


def _coerce(value: Any, annotation: Any, *, tool: str, parameter: str) -> Any:
    """Turn the model's string into the type the tool declares.

    Deliberately narrow: `date`, `Decimal`, `int`, `bool`, `tuple[str, ...]` and `str`. Anything
    else the generator refuses to expose at all, rather than guessing here.
    """
    if value is None:
        return None
    origin = getattr(annotation, "__origin__", None)
    if origin is tuple or annotation in (tuple, "tuple[str, ...]"):
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return tuple(value)

    text = value if isinstance(value, str) else str(value)
    convert = _CONVERTERS.get(_unwrap_optional(annotation))
    if convert is None:
        return text
    try:
        return convert(text)
    except (ValueError, InvalidOperation) as exc:
        raise ValueError(
            f"{tool}({parameter}={value!r}): {exc}. Expected "
            f"{getattr(annotation, '__name__', annotation)}"
            + (", e.g. 2026-08-17" if annotation is date else "")
        ) from exc


def _unwrap_optional(annotation: Any) -> Any:
    """`date | None` -> `date`.

    Without this, six catalogue parameters skipped coercion -- including two dates on
    `edgar.search_filings`, which is on a *published* manifest. The model's string reached the tool
    and failed inside it with an AttributeError, which is verbatim the failure the wrapper exists to
    prevent and which the commit introducing it claimed to have closed.
    """
    args = [a for a in typing.get_args(annotation) if a is not type(None)]
    return args[0] if len(args) == 1 else annotation


#: The only annotations the generator will coerce. Anything else is refused at generation time
#: rather than guessed at call time -- a wrong guess here means the tool runs on the wrong value.
_CONVERTERS: dict[Any, Callable[[str], Any]] = {
    date: date.fromisoformat,
    datetime: datetime.fromisoformat,
    Decimal: Decimal,
    int: int,
    bool: lambda text: text.strip().lower() in {"true", "1", "yes"},
    str: lambda text: text,
}


# --------------------------------------------------------------------------- what was observed


def _locate(spec: packs.ToolSpec, result: object, args: dict[str, Any]) -> str | None:
    """The specific resource this evidence came from, if the tool can say.

    Previously a constant per namespace, held in the platform: `ecb_fx` got one service URL
    identical for every call and everything else got `None` -- so for two of the three published
    investigators no citation could carry a source_uri at all, and the criterion requiring one was
    unsatisfiable. A tool that genuinely has no URI says so, which is better than a constant that
    identifies nothing.
    """
    if spec.locate is None:
        return spec.default_uri(args)
    try:
        return spec.locate(result) or spec.default_uri(args)
    except Exception:  # noqa: BLE001
        logger.exception("source-uri projection failed for %s", spec.name)
        return spec.default_uri(args)


def _observe(spec: packs.ToolSpec, result: object, args: dict[str, Any]) -> dict[str, str]:
    """Ask the process what this result lets a verdict cite, and store it as opaque text.

    The platform does not know what `rate_date` means and must not: the projection is declared per
    tool by the pack, so a transfer-agency pack projects share counts through the same mechanism
    without this module changing. A tool with no projection contributes no facts -- honest, since it
    can still be cited as having been called.
    """
    if spec.observe is None:
        return {}
    try:
        projected = stringify(spec.observe(result, args))
        undeclared = sorted(set(projected) - set(spec.facts))
        if undeclared:
            # Refused rather than dropped. Silently filtering meant `_observe_security` projected
            # `domicile`, nothing declared it, and the fact the corporate-action cross-check turns
            # on was uncitable with no test failing.
            raise ValueError(
                f"{spec.name} projected undeclared fact(s) {undeclared}; declared: "
                f"{sorted(spec.facts)}"
            )
        return projected
    except Exception:  # noqa: BLE001
        # A broken projection must not fail the tool call: the observation is still recorded, with
        # no citable facts, and the requirement check will refuse a verdict that needed them.
        logger.exception("observation projection failed for %s", spec.name)
        return {}


def _summarise(tool: str, result: object) -> str:
    if result is None:
        return f"{tool} returned nothing"
    if isinstance(result, list):
        return f"{tool} returned {len(result)} record(s)"
    return f"{tool} returned {str(result)[:160]}"


# --------------------------------------------------------------------------- generation


def _validate(manifest: AgentManifest) -> None:
    """Refuse a surface the model could not use, or that names tools nobody declares."""
    catalogue = packs.catalogue()
    for name in manifest.allowed_tools:
        spec = catalogue.get(name)
        if spec is None:
            raise SurfaceInvalid(
                f"{manifest.agent_id} is allowed {name!r}, which no registered process declares. "
                f"A manifest naming a nonexistent tool is a deployment defect, not a runtime "
                f"surprise. Declared: {sorted(catalogue)}"
            )
        if not spec.description.strip():
            raise SurfaceInvalid(
                f"{name!r} has no description, so a model cannot tell it apart from its "
                f"neighbours. Declare one on its ToolSpec."
            )


def build(
    manifest: AgentManifest,
    *,
    case_id: str,
    trace_id: str | None,
    store: ObservationStore,
    budget: int = DEFAULT_CALL_BUDGET,
) -> list[Callable[..., Any]]:
    """The tools this agent may call on this case, as plain functions ADK can wrap.

    Returns callables rather than `FunctionTool`s so this module does not import ADK: the platform
    layer stays free of the agent framework, and a test can invoke a wrapper directly.
    """
    import inspect

    _validate(manifest)
    agent_ref = manifest.ref
    catalogue = packs.catalogue()
    calls = {"n": 0}

    def make(name: str) -> Callable[..., Any]:
        spec = catalogue[name]
        signature = inspect.signature(spec.fn)
        # `get_type_hints`, not `signature.parameters[..].annotation`. Every tool module uses
        # `from __future__ import annotations`, so the annotation is the *string* "date" and a
        # lookup keyed by the `date` class silently matched nothing -- coercion was skipped and the
        # tool received the model's raw string, which is the exact failure this wrapper exists to
        # prevent. It failed inside the tool with a TypeError rather than at the boundary.
        try:
            resolved = typing.get_type_hints(spec.fn)
        except Exception:  # noqa: BLE001  # pragma: no cover - a tool with an unresolvable hint
            logger.warning("cannot resolve type hints for %s; treating arguments as text", name)
            resolved = {}
        hints = {p.name: resolved.get(p.name, str) for p in signature.parameters.values()}

        def wrapper(**kwargs: Any) -> Any:
            try:
                return _invoke(**kwargs)
            except (ToolBudgetExhausted, ValueError, gateway.ToolFailed) as exc:
                # Returned, not raised. ADK re-raises a tool exception out of `runner.run_async`
                # unless a callback handles it, so a budget message written as an instruction to
                # the model -- "state what you concluded, or return UNKNOWN" -- was undeliverable,
                # and a coercion error the model was meant to fix on its next turn was a stack
                # trace instead. `{"error": ...}` is ADK's own convention and its
                # `_detect_error_in_response` already recognises it.
                #
                # `ToolFailed` is included: "no notice is recorded for that date" is something the
                # model can act on, and letting it propagate made ADK log a full traceback for an
                # ordinary negative result. A control *refusing* -- ContentBlocked,
                # ExtractionRejected -- is deliberately absent, because those must reach the
                # investigator's refusal path as themselves.
                logger.info("%s returned an error to the model: %s", name, exc)
                return {"error": str(exc)}

        def _invoke(**kwargs: Any) -> Any:
            if calls["n"] >= budget:
                raise ToolBudgetExhausted(
                    f"{agent_ref} used its {budget}-call budget on {case_id}. State what you "
                    f"concluded from the evidence you already have, or return UNKNOWN."
                )
            calls["n"] += 1

            coerced = {
                key: _coerce(value, hints.get(key, str), tool=name, parameter=key)
                for key, value in kwargs.items()
                if value is not None
            }
            # Through the gateway, always. Resolving spec.fn here would move P-001 and P-006 out
            # of the runtime and leave no record of the call in the governance log.
            result = gateway.call_tool(name, **coerced)

            args = ",".join(f"{k}={coerced[k]}" for k in sorted(coerced))
            digest = digest_of(result)
            recorded = store.record(
                Observation(
                    observation_id=observation_id(case_id, name, args, digest),
                    case_id=case_id,
                    trace_id=trace_id,
                    agent_ref=agent_ref,
                    tool=name,
                    args=args,
                    digest=digest,
                    retrieved_at=utcnow(),
                    source=spec.source,
                    source_uri=_locate(spec, result, coerced),
                    observed=_observe(spec, result, coerced),
                    trusted=not spec.untrusted_output,
                    summary=_summarise(name, result),
                )
            )
            # Return the observation id alongside the result, because a verdict cites evidence by
            # id and the model has no other way to learn one. Without this the surface worked, the
            # store filled up, and `Verdict.citations` could never be legitimately populated -- the
            # evidence mechanism was unusable and every test still passed, because they all built
            # observations directly instead of going through a model.
            return {
                "observation_id": recorded.observation_id,
                "result": _renderable(result),
            }

        # A dotted name cannot be `def`d, so the ADK-facing name substitutes underscores while
        # `nav_tool_name` keeps the catalogue's exact string -- the audit record and the model's
        # call site must not diverge.
        wrapper.__name__ = name.replace(".", "__")
        wrapper.__qualname__ = wrapper.__name__
        wrapper.__doc__ = _document(name, spec.description, signature, hints)
        wrapper.nav_tool_name = name  # type: ignore[attr-defined]

        # Publish an explicit, all-string signature. Without this the wrapper is `(**kwargs)` and
        # ADK derives `parameters_json_schema: None` -- a declaration with no parameters, which
        # Gemini can only call with no arguments. Measured against ADK 2.7.1, and it is why the
        # coercion above exists: every parameter arrives as text and is converted here.
        wrapper.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
            [
                inspect.Parameter(
                    p.name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=str,
                    default=inspect.Parameter.empty
                    if p.default is inspect.Parameter.empty
                    else None,
                )
                for p in signature.parameters.values()
            ],
            return_annotation=str,
        )
        wrapper.__annotations__ = {
            **{p.name: str for p in signature.parameters.values()},
            "return": str,
        }
        return wrapper

    return [make(name) for name in manifest.allowed_tools]


def _document(name: str, description: str, signature: Any, hints: dict[str, Any]) -> str:
    """The docstring ADK turns into the model's tool description.

    Argument types are spelled out because every parameter is exposed as a string: the model has to
    be told that `day` is an ISO date and not a free-form one.
    """
    lines = [
        f"{description} (tool: {name})",
        "",
        "Returns a mapping with `observation_id` and `result`. Cite the `observation_id` in your "
        "verdict for any fact you draw from `result`; a claim you cannot cite will be rejected.",
        "",
        "Args:",
    ]
    for parameter in signature.parameters.values():
        annotation = hints.get(parameter.name, str)
        hint = {
            date: "ISO date, e.g. 2026-08-17",
            datetime: "ISO timestamp",
            Decimal: "decimal number as text",
            int: "integer as text",
        }.get(annotation, "text")
        optional = parameter.default is not parameter.empty
        lines.append(f"    {parameter.name}: {hint}{' (optional)' if optional else ''}")
    return "\n".join(lines) if signature.parameters else "\n".join(lines[:3])


def _renderable(value: Any) -> Any:
    """What the model sees. Pydantic models become plain dicts; Decimals and dates become text.

    ADK serialises return values itself, but it renders a `Position` as an opaque object, and a
    model cannot reason about a holding it cannot read.
    """
    if hasattr(value, "model_dump"):
        return _renderable(value.model_dump())
    if isinstance(value, dict):
        return {str(k): _renderable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_renderable(v) for v in value]
    if isinstance(value, Decimal | date | datetime):
        return str(value)
    return value
