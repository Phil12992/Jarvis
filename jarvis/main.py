"""JARVIS - Einstiegspunkt.

Startet Web-Chat und Telegram parallel und faengt beide sauber ab. Wenn ein
Kanal ausfaellt, laeuft der andere weiter: ein abgestuerzter Telegram-Poll
soll nicht bedeuten, dass das Web-Chat weg ist.

Wichtig: der Import von jarvis.tools.homelab ist nicht optional. Die
Registrierung der Werkzeuge passiert als Import-Seiteneffekt, ohne diesen
Import waere die Registry beim Start leer und der Agent haette keine
Werkzeuge.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys

import structlog

# Werkzeuge registrieren sich beim Import. Muss vor get_agent() passieren.
from jarvis.tools import homelab  # noqa: F401
from jarvis.config import get_settings, setup_logging


async def _housekeeping() -> None:
    """Raeumt alte Sessions weg und schneidet Memory zu.

    Laeuft alle 6 Stunden. Ohne das waechst die Datenbank unbegrenzt, und
    auf einem CT mit 24 GB Disk ist das die wahrscheinlichste Todesursache
    nach "RAM voll".
    """
    from jarvis.memory.store import get_memory

    s = get_settings()
    mem = get_memory()
    while True:
        try:
            await asyncio.sleep(6 * 3600)
            reaped = await mem.reap_idle(s.session_idle_ttl_hours)
            pruned = await mem.prune_memories(keep=800)
            stats = await mem.stats()
            log.info("housekeeping", sessions_entfernt=reaped, memories_entfernt=pruned, **stats)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.error("housekeeping_fehler", fehler=repr(exc))


async def _periodic_skill_reload() -> None:
    """Skills neu einlesen, ohne den Agent neu zu starten. Praktisch, wenn
    du einen Skill auf dem Laptop schreibst und ihn sofort nutzen willst."""
    from jarvis.skills.loader import get_skill_store

    while True:
        await asyncio.sleep(300)
        try:
            if get_skill_store().reload_if_stale():
                log.info("skills_neu_geladen", anzahl=get_skill_store().count)
        except Exception as exc:  # noqa: BLE001
            log.warning("skill_reload_fehler", fehler=repr(exc))


async def amain() -> int:
    s = get_settings()
    setup_logging(s.log_level)
    log = structlog.get_logger("jarvis.main")

    from jarvis.channels import telegram, web
    from jarvis.config import get_settings as _s
    from jarvis.llm.router import warm_up
    from jarvis.skills.loader import get_skill_store
    from jarvis.tools.registry import registry

    log.info(
        "start",
        modelle=s.model_chain,
        homelab=s.homelab_enabled,
        code_runner=s.code_runner_enabled,
        web_port=s.web_port,
        telegram=bool(s.telegram_bot_token),
    )

    if not s.openrouter_api_key:
        log.error("kein_api_key", hinweis="OPENROUTER_API_KEY fehlt in .env - ohne Modell geht nichts.")

    n_skills = get_skill_store().reload()
    log.info(
        "inventar",
        skills=n_skills,
        tools=len(registry.all()),
        modelle=len(s.model_chain),
    )

    if not registry.all():
        log.warning("keine_werkzeuge_registriert")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    tasks: list[asyncio.Task[None]] = [
        asyncio.create_task(web.run(), name="web"),
        asyncio.create_task(telegram.run(), name="telegram"),
        asyncio.create_task(_housekeeping(), name="housekeeping"),
        asyncio.create_task(_periodic_skill_reload(), name="skill-reload"),
        asyncio.create_task(warm_up(), name="warmup"),
    ]

    await stop.wait()
    log.info("shutdown_erwartet")
    for t in tasks:
        t.cancel()
    for t in tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await t
    log.info("beendet")
    return 0


def main() -> int:
    try:
        return asyncio.run(amain())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        structlog.get_logger("jarvis.main").critical("absturz", fehler=repr(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
