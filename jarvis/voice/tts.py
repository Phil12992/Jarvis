"""Text-to-Speech mit Piper.

Piper ist hier die richtige Wahl, weil es auf einem einzelnen Kern in
Echtzeit synthetisiert. XTTS und Coqui brauchen eine GPU oder deutlich mehr
RAM, beides nicht vorhanden. Piper-Voices sind ~60 MB und klingen brauchbar.

Ist piper nicht installiert (pip install 'jarvis[tts]'), faellt der Code auf
stille zurueck: Text wird normal gesendet, der Channel erkennt das an
einem None-Ergebnis.
"""

from __future__ import annotations

import asyncio
import shutil
import wave
from functools import lru_cache
from pathlib import Path

import structlog

from jarvis.config import get_settings

log = structlog.get_logger("jarvis.tts")

PIPER_BIN = shutil.which("piper")
_VOICE_DIR = Path("/data/audio/voices")


def tts_ready() -> bool:
    return bool(get_settings().tts_enabled and PIPER_BIN)


@lru_cache(maxsize=1)
def _voice_path(voice: str) -> Path | None:
    for base in (_VOICE_DIR, Path.home() / ".local/share/piper-voices"):
        p = base / f"{voice}.onnx"
        if p.exists():
            return p
    for p in _VOICE_DIR.glob("*.onnx") if _VOICE_DIR.exists() else []:
        log.info("voice_gefunden", datei=p.name)
        return p
    return None


async def synthesize(text: str, *, max_chars: int = 3000) -> bytes | None:
    """Text zu OGG/WAV. Gibt None zurueck wenn TTS nicht verfuegbar ist."""
    s = get_settings()
    if not s.tts_enabled or not PIPER_BIN:
        return None

    text = text.strip()
    if not text:
        return None
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(".", 1)[0] + "."

    voice = _voice_path(s.tts_voice)
    cmd = [PIPER_BIN, "--output_file", "-"]
    if voice:
        cmd += ["--model", str(voice)]
    else:
        log.warning("tts_voice_fehlt", gesucht=s.tts_voice)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        log.error("tts_start_fehler", fehler=repr(exc))
        return None

    stdout, stderr = await proc.communicate(text.encode("utf-8"))
    if proc.returncode != 0:
        log.error("tts_fehler", code=proc.returncode, err=stderr.decode()[:300])
        return None

    audio = _to_ogg(stdout)
    return audio or None


def _to_ogg(raw_wav: bytes) -> bytes | None:
    """Piper gibt RIFF/WAV. Telegram nimmt Ogg/Opus, WAV auch - aber
    Telegram ist bei WAV unzuverlaessig, deshalb wird konvertiert wenn
    ffmpeg da ist, sonst bleibt WAV."""
    if not raw_wav:
        return None

    if b"RIFF" not in raw_wav[:4]:
        return None

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.info("kein_ffmpeg_wav_wird_gesendet")
        return raw_wav

    try:
        import subprocess  # noqa: S404

        out = subprocess.run(  # noqa: S603
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1"],
            input=raw_wav, capture_output=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("ffmpeg_fehler", fehler=repr(exc))
        return raw_wav

    return out.stdout if out.returncode == 0 and out.stdout else raw_wav


def wav_info(path: Path) -> dict[str, float | int]:
    """Kurze Diagnose: Laenge und Format einer WAV-Datei."""
    with wave.open(str(path), "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate() or 1
        return {
            "sekunden": round(frames / rate, 2),
            "khz": round(w.getframerate() / 1000, 1),
            "kanal": w.getnchannels(),
        }

