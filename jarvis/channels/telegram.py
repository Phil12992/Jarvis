"""Telegram-Channel.

Long Polling, kein Webhook. Auf einem CT hinter NAT ist das der einzige Weg,
der ohne TLS-Zertifikat und ohne Portfreigabe funktioniert.

Formatierung liegt in jarvis.channels.formatting, nicht hier. Grund: das ist
Logik, kein Netzwerk, und so bleibt sie ohne die Telegram-Dep testbar.
"""

from __future__ import annotations

import asyncio
import html
from typing import Any

import structlog
from telegram import Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from jarvis.agent.loop import get_agent, new_session_id
from jarvis.channels.formatting import MAX_MESSAGE, md_to_html, split_message
from jarvis.config import get_settings
from jarvis.memory.store import get_memory
from jarvis.skills.loader import get_skill_store
from jarvis.tools.registry import registry
from jarvis.voice import stt, tts

log = structlog.get_logger("jarvis.telegram")

_session_by_chat: dict[int, str] = {}
_busy: set[int] = set()


def _authorized(update: Update) -> bool:
    s = get_settings()
    user = update.effective_user
    if user is None:
        return False
    if not s.telegram_allowed_users:
        # Ohne Allowlist laeuft der Bot offen. Das ist im LAN okay,
        # sobald du ihn aus dem LAN loeschst nicht mehr.
        log.warning("telegram_ohne_allowlist_offen")
        return True
    return user.id in s.telegram_allowed_users


async def _send(update: Update, text: str, *, as_voice: bool = False) -> None:
    if not text.strip():
        return
    chat = update.effective_chat
    if chat is None:
        return

    if as_voice and tts.tts_ready():
        audio = await tts.synthesize(text)
        if audio:
            try:
                await chat.send_voice(voice=audio, caption=None)
                return
            except TelegramError as exc:
                log.warning("voice_send_fehler", fehler=repr(exc))

    for chunk in split_message(text):
        try:
            await chat.send_message(
                chunk, parse_mode="HTML", disable_web_page_preview=True
            )
        except TelegramError as exc:
            log.error("send_fehler", fehler=repr(exc))
            # parse_mode ist der haeufigste Grund. Einmal ohne versuchen.
            try:
                await chat.send_message(chunk, disable_web_page_preview=True)
            except TelegramError as exc2:
                log.error("send_fehler_auch_ohne_format", fehler=repr(exc2))


async def _handle_text(update: Update, context: ContextTypes.DEFAULT_TYPES) -> None:
    if not _authorized(update):
        return
    chat = update.effective_chat
    msg = update.effective_message
    if chat is None or msg is None:
        return

    if chat.id in _busy:
        await chat.send_message("Ich arbeite noch an deiner letzten Nachricht.")
        return
    _busy.add(chat.id)
    session = _session_by_chat.setdefault(chat.id, new_session_id("telegram", str(chat.id)))

    try:
        await chat.send_action(ChatAction.TYPING)
        reply = await get_agent().turn(
            session, msg.text or "", channel="telegram", peer=str(chat.id)
        )
        await _send(update, md_to_html(reply.text) if reply.text else "…")
    finally:
        _busy.discard(chat.id)


async def _handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPES) -> None:
    if not _authorized(update):
        return
    chat = update.effective_chat
    msg = update.effective_message
    voice = msg.voice if msg else None
    if chat is None or voice is None:
        return

    if not stt.stt_ready():
        await chat.send_message(
            "Sprache ist nicht eingerichtet. Setze STT_PROVIDER=groq und "
            "STT_API_KEY in der .env, dann kann ich Sprachnachrichten verstehen."
        )
        return

    if chat.id in _busy:
        return
    _busy.add(chat.id)
    session = _session_by_chat.setdefault(chat.id, new_session_id("telegram", str(chat.id)))

    try:
        await chat.send_action(ChatAction.TYPING)
        text = await stt.transcribe_telegram_voice(voice)
        if not text:
            await chat.send_message("Ich habe die Sprachnachricht nicht verstanden.")
            return

        await chat.send_message(f"_{html.escape(text)}_")
        reply = await get_agent().turn(
            session, text, channel="telegram", peer=str(chat.id)
        )
        await _send(update, md_to_html(reply.text) if reply.text else "…", as_voice=True)
    finally:
        _busy.discard(chat.id)


async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPES) -> None:
    if not _authorized(update):
        return
    chat = update.effective_chat
    if chat is None:
        return
    _session_by_chat[chat.id] = new_session_id("telegram", str(chat.id))
    await chat.send_message(
        "JARVIS läuft.\n\n"
        "/help - was ich kann\n"
        "/tools - meine Werkzeuge\n"
        "/skills - verfuegbare Skills\n"
        "/memory - was ich ueber dich weiss\n"
        "/reset - neues Gedaechtnis fuer diese Sitzung\n\n"
        "Schreib einfach los, oder schick eine Sprachnachricht."
    )


async def _cmd_tools(update: Update, context: ContextTypes.DEFAULT_TYPES) -> None:
    if not _authorized(update):
        return
    await _send(update, f"<pre>{html.escape(registry.describe())}</pre>")


async def _cmd_skills(update: Update, context: ContextTypes.DEFAULT_TYPES) -> None:
    if not _authorized(update):
        return
    store = get_skill_store()
    n = store.reload_if_stale() or store.count
    if not n:
        await _send(update, "Keine Skills geladen. Skills liegen in /data/skills als .md mit Frontmatter.")
        return
    await _send(update, f"<pre>{html.escape(store.index_prompt())}</pre>")


async def _cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPES) -> None:
    if not _authorized(update):
        return
    mem = await get_memory().recall(limit=25)
    if not mem:
        await _send(update, "Ich habe noch nichts ueber dich gespeichert.")
        return
    lines = [f"• {m['content']}  _(w{m['importance']}, {m['hits']}x)_" for m in mem]
    await _send(update, "Das weiss ich:\n\n" + "\n".join(lines))


async def _cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPES) -> None:
    if not _authorized(update):
        return
    chat = update.effective_chat
    if chat is None:
        return
    _session_by_chat[chat.id] = new_session_id("telegram", str(chat.id))
    await chat.send_message("Neue Sitzung. Kontext ist leer, das Langzeitgedaechtnis bleibt.")


async def _cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPES) -> None:
    if not _authorized(update):
        return
    chat = update.effective_chat
    if chat is None:
        return
    await chat.send_message(
        "Ich kann:\n"
        "• deinen Proxmox nachsehen (VMs, Container, RAM, Storage)\n"
        "• im CT Logs lesen\n"
        "• merken, was du mir sagst\n"
        "• auf Deutsch antworten, per Sprache\n\n"
        "Ich laufe auf einem Container mit 3 GB. Ich kann keine schweren "
        "Rechnungen fuer dich machen und kein Code laeuft bei mir."
    )


async def _error(update: object, context: ContextTypes.DEFAULT_TYPES) -> None:
    log.error("telegram_fehler", fehler=repr(context.error))


def build() -> Application:
    s = get_settings()
    if not s.telegram_bot_token:
        log.warning("telegram_deaktiviert_kein_token")
        return None  # type: ignore[return-value]

    app = (
        Application.builder()
        .token(s.telegram_bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("help", _cmd_help))
    app.add_handler(CommandHandler("tools", _cmd_tools))
    app.add_handler(CommandHandler("skills", _cmd_skills))
    app.add_handler(CommandHandler("memory", _cmd_memory))
    app.add_handler(CommandHandler("reset", _cmd_reset))
    app.add_handler(MessageHandler(filters.VOICE, _handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_text))
    app.add_error_handler(_error)
    return app


async def _post_init(app: Application) -> None:
    bot = app.bot
    me = await bot.get_me()
    log.info("telegram_verbunden", bot=me.username, id=me.id)
    s = get_settings()
    if not s.telegram_allowed_users:
        log.warning(
            "telegram_allowlist_leer",
            hinweis=f"Schreibe /start an @{me.username} und setze dann "
            f"TELEGRAM_ALLOWED_USERS={me.id}",
        )


async def _post_shutdown(app: Application) -> None:
    log.info("telegram_getrennt")


async def run() -> None:
    app = build()
    if app is None:
        return
    log.info("telegram_start", modus="long-polling")
    await app.start()
    try:
        await app.updater.start_polling(drop_pending_updates=True)
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
