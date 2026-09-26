"""Model-Routing mit Fallback-Kette.

Jedes Modell in der Kette wird der Reihe nach probiert, bis eines antwortet.
Der Router haelt bewusst keine eigene Retry-Logik: litellm macht das intern,
und zwei Retry-Schichten uebereinander erzeugen genau die Endlosschleife,
die du auf einem CT mit 3 GB nicht willst.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import litellm
import structlog

from jarvis.config import Settings, get_settings
from jarvis.llm.errors import JarvisError, LLMUnavailable, ToolDenied, ToolFailed

log = structlog.get_logger("jarvis.llm")

# Fehler, die einen Fallback rechtfertigen. Ein 400er "invalid tool schema"
# ist keiner davon - das ist ein Bug im eigenen Prompt, den wiederholt man nicht.
FALLBACK_EXCEPTIONS: tuple[type[Exception], ...] = (
    litellm.RateLimitError,
    litellm.ServiceUnavailableError,
    litellm.APIConnectionError,
    litellm.Timeout,
    litellm.InternalServerError,
)



@dataclass(slots=True)
class Completion:
    text: str
    model: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)
    elapsed_ms: int = 0


@dataclass(slots=True)
class _Attempt:
    model: str
    ok: bool
    ms: int
    error: str = ""


class Router:

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        litellm.drop_params = True
        litellm.suppress_debug_info = True
        litellm.set_verbose = False
        litellm.num_retries = 2
        if self.settings.openrouter_api_key:
            litellm.api_key = self.settings.openrouter_api_key

    @property
    def chain(self) -> list[str]:
        return self.settings.model_chain

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        model_override: str | None = None,
    ) -> Completion:
        started = time.monotonic()
        chain = [model_override] if model_override else self.chain
        attempts: list[_Attempt] = []

        for model in chain:
            t0 = time.monotonic()
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": self.settings.llm_temperature,
                    "max_tokens": self.settings.llm_max_tokens,
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"

                resp = await litellm.acompletion(**kwargs)
            except FALLBACK_EXCEPTIONS as exc:
                ms = int((time.monotonic() - t0) * 1000)
                attempts.append(_Attempt(model, False, ms, f"{type(exc).__name__}: {exc}"))
                log.warning(
                    "modell_fehler",
                    model=model,
                    fehler=type(exc).__name__,
                    ms=ms,
                    rest=self.chain.index(model) + 1 if model in self.chain else 0,
                )
                continue
            except Exception as exc:
                # Unerwartet: nicht weiterfallen, sondern laut sein. Ein stilles
                # Weiterfallen hier macht echte Bugs unauffindbar.
                ms = int((time.monotonic() - t0) * 1000)
                attempts.append(_Attempt(model, False, ms, f"{type(exc).__name__}: {exc}"))
                log.error("modell_unerwarteter_fehler", model=model, fehler=repr(exc))
                continue

            ms = int((time.monotonic() - t0) * 1000)
            attempts.append(_Attempt(model, True, ms))
            choice = resp.choices[0]
            msg = choice.message
            tool_calls = [
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                }
                for tc in (msg.tool_calls or [])
            ]
            content = msg.content or ""
            if not content and not tool_calls:
                content = "Das Modell hat eine leere Antwort geliefert."

            log.info("modell_ok", model=model, ms=ms, tools=len(tool_calls))
            return Completion(
                text=content,
                model=model,
                tool_calls=tool_calls,
                finish_reason=choice.finish_reason or "",
                attempts=[vars(a) for a in attempts],
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )

        detail = "; ".join(f"{a.model}: {a.error}" for a in attempts if not a.ok)
        raise LLMUnavailable(f"Alle {len(chain)} Modelle gescheitert. {detail}")

    async def stream(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        """Token-Stream. Bewusst ohne Fallback: ein Stream laesst sich nicht
        halb zurueckrollen. Der Aufrufer entscheidet, ob ein Abbruch bei
        Mid-Stream-Fehlern verkraftbar ist."""
        model = self.chain[0]
        try:
            stream = await litellm.acompletion(
                model=model,
                messages=messages,
                temperature=self.settings.llm_temperature,
                max_tokens=self.settings.llm_max_tokens,
                stream=True,
            )
        except FALLBACK_EXCEPTIONS as exc:
            raise LLMUnavailable(f"{model} nicht erreichbar: {exc}") from exc

        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

    async def health(self) -> list[dict[str, Any]]:
        """Pro Modell ein 1-Token-Probe. Nur fuer Statusanzeigen, nicht als Gate."""
        out: list[dict[str, Any]] = []
        for model in self.chain:
            t0 = time.monotonic()
            try:
                await litellm.acompletion(
                    model=model,
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=1,
                )
                out.append(
                    {
                        "model": model,
                        "ok": True,
                        "ms": int((time.monotonic() - t0) * 1000),
                    }
                )
            except Exception as exc:
                out.append(
                    {
                        "model": model,
                        "ok": False,
                        "error": type(exc).__name__,
                    }
                )
        return out


_router: Router | None = None


def get_router() -> Router:
    global _router
    if _router is None:
        _router = Router()
    return _router


async def warm_up() -> None:
    """Einmaliger Aufruf beim Start, damit der erste echter Chat nicht
    mit einem Kaltstart des Providers bezahlt wird."""
    router = get_router()
    try:
        await asyncio.wait_for(router.health(), timeout=20)
    except TimeoutError:
        log.warning("llm_warmup_timeout")
