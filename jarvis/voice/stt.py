"""Speech-to-Text.

Nur Cloud. Whisper lokal auf dem i5-2520M (2 Kerne, kein AVX2) ergibt
etwa 1 Token pro Sekunde Audio, also braucht eine 20-Sekunden-Nachricht
etwa eine Minute. Das ist keine Verknappung, das ist unbrauchbar.

Groq ist hier die Empfehlung: kostenloses Tier fuer whisper-large-v3-turbo,
und es ist schnell genug, dass die Antwortgefuehl halbwegs direkt bleibt.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import httpx
import structlog

from jarvis.config import get_settings

log = structlog.get_logger("jarvis.stt")

# Groq und OpenAI nutzen beide /audio/transcriptions, nur Base-URL und
# Modellname unterscheiden sich. Deshalb ein Adapter statt zweier Wege.
PROVIDERS: dict[str, dict[str, Any]] = {
    "groq": {
        "url": "https://api.groq.com/openai/v1/audio/transcriptions",
        "model": "whisper-large-v3-turbo",
    },
    "openai": {
        "url": "https://api.openai.com/v1/audio/transcriptions",
        "model": "whisper-1",
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/audio/transcriptions",
        "model": "openai/whisper-1",
    },
}


def stt_ready() -> bool:
    s = get_settings()
    return bool(s.stt_provider and s.stt_api_key)


async def transcribe(audio: bytes, mime: str = "audio/ogg") -> str:
    """OGG/Opus von Telegram in Text. Gibt "" zurueck, wenn STT nicht
    konfiguriert oder fehlgeschlagen ist - der Channel faellt dann auf einen
    Hinweistext zurueck statt zu crashen."""
    s = get_settings()
    if not stt_ready():
        log.info("stt_nicht_konfiguriert")
        return ""

    provider = PROVIDERS.get(s.stt_provider)
    if provider is None:
        log.warning("stt_unbekannter_provider", provider=s.stt_provider)
        return ""

    suffix = ".ogg" if "ogg" in mime or "opus" in mime else ".mp3"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(audio)
        tmp.close()

        with open(tmp.name, "rb") as fh:
            files = {"file": (f"audio{suffix}", fh, mime)}
            data = {
                "model": s.stt_model or provider["model"],
                "language": "de",
                "response_format": "json",
            }
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(
                    provider["url"],
                    files=files,
                    data=data,
                    headers={"Authorization": f"Bearer {s.stt_api_key}"},
                )
    except httpx.HTTPError as exc:
        log.error("stt_fehler", fehler=repr(exc))
        return ""
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    if r.status_code != 200:
        log.error("stt_http_fehler", status=r.status_code, body=r.text[:300])
        return ""

    try:
        return str(r.json().get("text", "")).strip()
    except ValueError:
        log.error("stt_kein_json", body=r.text[:200])
        return ""


async def transcribe_telegram_voice(voice_file: Any) -> str:
    """voice_file ist ein telegram.Voice.

    Wichtig: Telegram zeigt dir im Client ein Transkript, aber der Bot sieht
    nur die OGG-Bytes. Was du siehst und was der Agent liest, sind zwei
    verschiedene Texte - das ist der Grund, warum es hier ueberhaupt STT
    braucht.
    """
    try:
        raw = await voice_file.download_as_bytearray()
    except Exception as exc:  # noqa: BLE001
        log.error("voice_download_fehler", fehler=repr(exc))
        return ""
    return await transcribe(bytes(raw), mime="audio/ogg")

