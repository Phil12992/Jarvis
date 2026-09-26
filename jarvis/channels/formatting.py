"""Textformatierung fuer Channel-Ausgaben.

Getrennt vom Telegram-Client, weil das zwei verschiedene Aufgaben sind:
hier passiert Logik, dort passiert Netzwerk-I/O. Ausserdem ist es dadurch
ohne python-telegram-bot testbar, was auf einem CT mit 3 GB den Unterschied
zwischen "Tests laufen" und "Tests werden nie ausgefuehrt" ausmacht.

Warum HTML statt MarkdownV2: Telegram hat 18 Zeichen, die in MarkdownV2
escaped werden muessen. Jedes vergessene Escape ist ein API-Fehler und die
Nachricht kommt nicht an. Bei HTML kann man den Rest einfach escapen und
damit ist der Fehlerfall ausgeschlossen.
"""

from __future__ import annotations

import html
import re

MAX_MESSAGE = 4096

_MD_BOLD = re.compile(r"(\*\*|__)(.+?)\1", re.S)

# Nur * und _ als Italic-Delimiter, KEIN / .
# Grund: der Telegram-Client erzeugt *fett* und _kursiv_, nie /kursiv/.
# Ein Slash-Delimiter wuerde stattdessen Pfade zerlegen: aus /opt/x /var
# wuerde <i>opt/x</i> var. Bei einer Maschine, die einem Agenten Pfade
# nennt, ist das ein produktiver Bug.
_MD_ITALIC = re.compile(r"(?<![\w*_])([*_])(?!\s)(.+?)(?<!\s)\1(?![\w*_])", re.S)
_MD_CODE = re.compile(r"`([^`\n]+)`")



def md_to_html(text: str) -> str:
    """Das kleine Markdown, das Telegram-Eingaben erzeugen, nach HTML.

    Reihenfolge ist wichtig: Code zuerst, weil der Inhalt dort nicht mehr
    als Formatting-Zeichen interpretiert werden darf. Alles wird vorher
    escaped, damit niemand HTML einschleusen kann.
    """
    placeholders: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        placeholders.append(m.group(1))
        return f"\x00{len(placeholders) - 1}\x00"

    staged = _MD_CODE.sub(_stash, text)
    staged = html.escape(staged, quote=False)
    staged = _MD_BOLD.sub(r"<b>\2</b>", staged)
    staged = _MD_ITALIC.sub(r"<i>\2</i>", staged)
    staged = staged.replace("  \n", "<br/>\n").replace("\n", "<br/>\n")

    for i, code in enumerate(placeholders):
        staged = staged.replace(f"\x00{i}\x00", f"<code>{html.escape(code)}</code>")
    return staged


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """An Absaetzen trennen, nicht an der Zeichengrenze.

    Ein Absatz, der laenger ist als das Limit, wird hart getrennt - sonst
    waere die Nachricht unmoeglich zu senden.
    """
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    buf = ""

    def _flush() -> None:
        nonlocal buf
        if not buf:
            return
        while len(buf) > limit:
            cut = buf.rfind(" ", 0, limit)
            if cut < limit // 2:
                cut = limit
            parts.append(buf[:cut].rstrip())
            buf = buf[cut:].lstrip()
        if buf:
            parts.append(buf)
        buf = ""

    for block in text.split("\n\n"):
        candidate = f"{buf}\n\n{block}" if buf else block
        if len(candidate) > limit:
            _flush()
            buf = block
        else:
            buf = candidate
    _flush()
    return [p for p in parts if p.strip()]


def strip_for_speech(text: str) -> str:
    """Text fuer TTS aufbereiten.

    TTS liest Markdown-Zeichen nicht vor, sondern liest sie buchstabiert.
    Also alles weg, was nicht gesprochen werden soll.
    """
    t = _MD_CODE.sub(r"\1", text)
    t = _MD_BOLD.sub(r"\2", t)
    t = _MD_ITALIC.sub(r"\2", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"[`*_>|#]", "", t)
    t = re.sub(r"\n{2,}", ". ", t)
    t = re.sub(r"\n", " ", t)
    return re.sub(r"\s{2,}", " ", t).strip()
