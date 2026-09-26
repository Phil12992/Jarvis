"""Tool-Registry mit Sicherheits-Tiers.

Der Agent kann Docker-Container und VMs stoppen. Ein Tier-System ist deshalb
nicht Beiwerk, es ist der Grund, warum diese Registry ueberhaupt eine eigene
Datei ist.

Drei Tiers:
  safe     read-only. Darf der Agent ohne Rueckfrage aufrufen.
  confirm  Veraendert Zustand. Der Channel muss beim Aufruf bestaetigen.
  danger   Unumkehrbar oder betrifft Dritte. Braucht eine explizite
           Allowlist-Eintragung, sonst ist das Tool gar nicht registriert.

Die Tiers werden beim Registrieren geprueft, nicht beim Aufrufen. Ein
Tippfehler im Handler-Pfad darf keine Sicherheitsstufe aufweichen.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, get_type_hints

import structlog

from jarvis.llm.errors import ToolDenied, ToolFailed

log = structlog.get_logger("jarvis.tools")

MAX_ARGS_CHARS = 2000


class Tier(StrEnum):
    SAFE = "safe"
    CONFIRM = "confirm"
    DANGER = "danger"



@dataclass(slots=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    tier: Tier
    handler: Callable[..., Awaitable[Any]]
    confirm_hint: str = ""
    enabled: bool = True
    calls: int = 0
    failures: int = 0

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }


@dataclass(slots=True)
class ToolResult:
    tool: str
    ok: bool
    output: str
    ms: int = 0
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def tool(
    name: str,
    description: str,
    schema: dict[str, Any],
    tier: Tier,
    *,
    confirm_hint: str = "",
    enabled: bool = True,
):
    """Registriert eine async-Funktion als Tool.

    Beispiel:
        @tool("docker_ps", "Listet Container", {...}, Tier.SAFE)
        async def docker_ps(all_: bool = False) -> str: ...
    """

    def deco(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        registry.register(
            Tool(
                name=name,
                description=description,
                schema=schema,
                tier=tier,
                handler=fn,
                confirm_hint=confirm_hint,
                enabled=enabled,
            )
        )
        return fn

    return deco


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._allowlist: set[str] = set()

    # --- Registrierung ------------------------------------------------------

    def register(self, t: Tool) -> None:
        if t.name in self._tools:
            raise ValueError(f"Tool {t.name!r} ist bereits registriert")
        if not inspect.iscoroutinefunction(t.handler):
            raise TypeError(f"Tool {t.name!r}: Handler muss async sein")
        if t.tier is Tier.DANGER and t.name not in self._allowlist:
            log.warning("danger_tool_nicht_aktiviert", tool=t.name)
            return
        self._tools[t.name] = t
        log.debug("tool_registriert", tool=t.name, tier=str(t.tier))

    def allow_danger(self, *names: str) -> None:
        for n in names:
            self._allowlist.add(n)
        log.warning("danger_tools_freigegeben", tools=list(names))

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[dict[str, Any]]:
        return [t.openai_schema() for t in self._tools.values() if t.enabled]

    def describe(self) -> str:
        """Menschenlesbares Inventar fuer /tools im Chat."""
        by_tier: dict[str, list[Tool]] = {}
        for t in self._tools.values():
            by_tier.setdefault(str(t.tier), []).append(t)
        lines: list[str] = []
        labels = {str(Tier.SAFE): "nur lesend", str(Tier.CONFIRM): "bestaetigung", str(Tier.DANGER): "gefahr"}
        for tier in (Tier.SAFE, Tier.CONFIRM, Tier.DANGER):
            group = by_tier.get(str(tier), [])
            if not group:
                continue
            lines.append(f"\n{labels[str(tier)].upper()} ({len(group)}):")
            for t in sorted(group, key=lambda x: x.name):
                flag = "" if t.enabled else "  [aus]"
                lines.append(f"  {t.name}{flag} - {t.description}")
        return "\n".join(lines) if lines else "Keine Tools registriert."

    # --- Ausfuehrung --------------------------------------------------------

    async def execute(
        self, name: str, args: dict[str, Any], *, confirmed: bool = False
    ) -> ToolResult:
        t = self._tools.get(name)
        if t is None:
            return ToolResult(
                tool=name,
                ok=False,
                output=f"Unbekanntes Tool: {name}",
                error="unknown_tool",
            )
        if not t.enabled:
            return ToolResult(
                tool=name, ok=False, output=f"Tool {name} ist deaktiviert.", error="disabled"
            )
        if t.tier is Tier.CONFIRM and not confirmed:
            return ToolResult(
                tool=name,
                ok=False,
                output=(
                    f"{name} aendert etwas und braucht deine Bestaetigung.\n"
                    f"Grund: {t.confirm_hint or 'siehe Beschreibung'}"
                ),
                error="needs_confirmation",
                meta={"tier": str(t.tier), "confirm_hint": t.confirm_hint, "args": args},
            )

        clean = _coerce_args(t, args)
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(t.handler(**clean), timeout=120)
        except TypeError as exc:
            t.failures += 1
            log.warning("tool_falsche_argumente", tool=name, fehler=str(exc))
            return ToolResult(
                tool=name, ok=False, output=f"Falsche Argumente: {exc}", error="bad_args"
            )
        except TimeoutError:
            t.failures += 1
            return ToolResult(
                tool=name, ok=False, output="Tool-Timeout nach 120 s.", error="timeout"
            )
        except Exception as exc:
            t.failures += 1
            log.error("tool_fehler", tool=name, fehler=repr(exc), args=_redact(clean))
            return ToolResult(
                tool=name, ok=False, output=f"Fehler: {type(exc).__name__}: {exc}", error="exception"
            )

        ms = int((time.monotonic() - started) * 1000)
        t.calls += 1
        text = result if isinstance(result, str) else str(result)
        return ToolResult(tool=name, ok=True, output=text[:MAX_ARGS_CHARS * 4], ms=ms)

    def stats(self) -> dict[str, Any]:
        return {
            "total": len(self._tools),
            "enabled": sum(1 for t in self._tools.values() if t.enabled),
            "calls": sum(t.calls for t in self._tools.values()),
            "failures": sum(t.failures for t in self._tools.values()),
            "tools": {t.name: {"tier": str(t.tier), "calls": t.calls} for t in self._tools.values()},
        }


def _coerce_args(t: Tool, args: dict[str, Any]) -> dict[str, Any]:
    """Modell-Ausgaben sind nie exakt das, was die Signatur will."""
    try:
        hints = get_type_hints(t.handler)
    except Exception:  # noqa: BLE001
        hints = {}
    sig = inspect.signature(t.handler)
    out: dict[str, Any] = {}
    for pname, param in sig.parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if pname in args:
            out[pname] = _coerce(args[pname], hints.get(pname, str))
        elif param.default is inspect.Parameter.empty:
            raise TypeError(f"Pflichtargument {pname} fehlt")
    return out


def _coerce(value: Any, hint: Any) -> Any:
    if value is None:
        return None
    target = hint if isinstance(hint, type) else None
    if target is bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "ja", "on"}
    if target is int and not isinstance(value, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if target is float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if target is str and not isinstance(value, str):
        if isinstance(value, (dict, list)):
            import json

            return json.dumps(value, ensure_ascii=False)
        return str(value)
    return value


_SECRET_HINTS = ("token", "secret", "password", "key", "auth")


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    """Nie ein Secret ins Log schreiben, auch nicht im Fehlerfall."""
    return {
        k: ("***" if any(h in k.lower() for h in _SECRET_HINTS) else v) for k, v in args.items()
    }


registry = Registry()
