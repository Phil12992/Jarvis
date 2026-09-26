"""Web-Chat: FastAPI + WebSocket.

Der Web-Channel ist kein zweites Gehirn, er ist ein zweites Fenster auf
denselben Agent. Session-IDs sind WebSocket-gebunden, damit ein
Wiederverbinden den Gelaeuft nicht verliert.

Auth ist ein optionales Bearer-Token. Ohne gesetztes Token laeuft das Ding
offen - im eigenen LAN ist das in Ordnung, sobald du eine Portfreigabe
machst, ist es das nicht mehr.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import structlog
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from jarvis.agent.loop import get_agent, new_session_id
from jarvis.config import get_settings
from jarvis.llm.router import get_router
from jarvis.memory.store import get_memory
from jarvis.skills.loader import get_skill_store
from jarvis.tools.registry import registry
from jarvis.voice import stt, tts

log = structlog.get_logger("jarvis.web")

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
MAX_UPLOAD = 25 * 1024 * 1024


def _check_auth(authorization: str | None, x_token: str | None) -> None:
    s = get_settings()
    if not s.web_auth_token:
        return
    given = ""
    if authorization and authorization.lower().startswith("bearer "):
        given = authorization[7:].strip()
    elif x_token:
        given = x_token.strip()
    if given != s.web_auth_token:
        raise HTTPException(status_code=401, detail="Ungültiges Token.")


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    """Memory und Skills werden genau einmal beim Start geladen.

    Das steht hier und nicht in run(), weil uvicorn bei reload und bei
    mehreren Workern den lifespan-Hook aufruft und run() nicht zwingend.
    """
    await get_memory().startup()
    n = get_skill_store().reload()
    log.info("startup", skills=n, tools=len(registry.all()))
    try:
        yield
    finally:
        await get_memory().close()
        log.info("shutdown")



async def auth_dep(
    authorization: str | None = Header(default=None),
    x_token: str | None = Header(default=None, alias="X-Auth-Token"),
) -> None:
    _check_auth(authorization, x_token)


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="JARVIS",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )

    if s.web_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=s.web_cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )

    # --- Seite -------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        f = WEB_DIR / "index.html"
        if not f.exists():
            return HTMLResponse("<h1>JARVIS</h1><p>web/index.html fehlt im Image.</p>", 500)
        return HTMLResponse(f.read_text(encoding="utf-8"))

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    # --- Status ------------------------------------------------------------

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status", dependencies=[Depends(auth_dep)])
    async def status() -> dict[str, Any]:
        mem = get_memory()
        store = get_skill_store()
        s = get_settings()
        return {
            "modelle": get_router().chain,
            "homelab": s.homelab_enabled,
            "code_runner": s.code_runner_enabled,
            "stt": stt.stt_ready(),
            "tts": tts.tts_ready(),
            "memory": await mem.stats(),
            "skills": store.count,
            "tools": registry.stats(),
        }

    @app.get("/api/llm-health", dependencies=[Depends(auth_dep)])
    async def llm_health() -> dict[str, Any]:
        return {"modelle": await get_router().health()}

    @app.get("/api/memory", dependencies=[Depends(auth_dep)])
    async def memory_list(q: str = "", limit: int = 40) -> dict[str, Any]:
        return {"erinnerungen": await get_memory().recall(q, limit=max(1, min(limit, 100)))}

    @app.delete("/api/memory/{memory_id}", dependencies=[Depends(auth_dep)])
    async def memory_delete(memory_id: int) -> dict[str, Any]:
        return {"geloescht": await get_memory().forget(memory_id)}

    @app.get("/api/tools", dependencies=[Depends(auth_dep)])
    async def tools() -> dict[str, Any]:
        return registry.stats(), {"beschreibung": registry.describe()}

    @app.get("/api/skills", dependencies=[Depends(auth_dep)])
    async def skills() -> dict[str, Any]:
        store = get_skill_store()
        return store.stats()

    # --- Chat (HTTP, fuer einfache Clients) --------------------------------

    @app.post("/api/chat", dependencies=[Depends(auth_dep)])
    async def chat(payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text", "")).strip()
        session = str(payload.get("session") or new_session_id("web", "http"))
        if not text:
            raise HTTPException(status_code=400, detail="Kein Text.")
        reply = await get_agent().turn(session, text, channel="web", peer="http")
        return {
            "session": session,
            "text": reply.text,
            "model": reply.model,
            "tool_runs": reply.tool_runs,
            "pending": reply.pending.tool if reply.pending else None,
            "ms": reply.ms,
            "error": reply.error,
        }

    # --- Chat (WebSocket) --------------------------------------------------

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        s = get_settings()
        token = websocket.query_params.get("token", "")
        if s.web_auth_token and token != s.web_auth_token:
            await websocket.close(code=4401, reason="unauthorized")
            return

        await websocket.accept()
        session = new_session_id("web", f"ws-{id(websocket)}")
        await websocket.send_json({"type": "session", "session": session})
        log.info("ws_verbunden", session=session)

        try:
            while True:
                raw = await websocket.receive_json()
                kind = str(raw.get("type", "chat"))
                if kind != "chat":
                    continue
                text = str(raw.get("text", "")).strip()
                if not text:
                    continue
                if websocket.client_state is not WebSocketState.CONNECTED:
                    break

                await websocket.send_json({"type": "thinking", "text": text})
                try:
                    reply = await get_agent().turn(
                        session, text, channel="web", peer=f"ws-{id(websocket)}"
                    )
                except Exception as exc:  # noqa: BLE001
                    log.error("ws_turn_fehler", fehler=repr(exc))
                    await websocket.send_json(
                        {"type": "error", "text": "Interner Fehler im Agent-Loop."}
                    )
                    continue

                await websocket.send_json(
                    {
                        "type": "reply",
                        "text": reply.text,
                        "model": reply.model,
                        "tool_runs": reply.tool_runs,
                        "pending": reply.pending.tool if reply.pending else None,
                        "ms": reply.ms,
                        "error": reply.error,
                    }
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.info("ws_getrennt", session=session, grund=repr(exc))
        finally:
            log.info("ws_geschlossen", session=session)

    # --- Sprache ------------------------------------------------------------

    @app.post("/api/stt", dependencies=[Depends(auth_dep)])
    async def speech_to_text(audio: UploadFile = File(...)) -> dict[str, Any]:
        if not stt.stt_ready():
            raise HTTPException(
                status_code=503,
                detail="STT ist nicht konfiguriert. Setze STT_PROVIDER und STT_API_KEY.",
            )
        data = await audio.read(MAX_UPLOAD)
        if not data:
            raise HTTPException(status_code=400, detail="Leere Audiodatei.")
        text = await stt.transcribe(data, mime=audio.content_type or "audio/webm")
        if not text:
            raise HTTPException(status_code=502, detail="Transkription fehlgeschlagen.")
        return {"text": text}

    @app.post("/api/tts", dependencies=[Depends(auth_dep)])
    async def text_to_speech(payload: dict[str, Any]) -> Any:
        if not tts.tts_ready():
            raise HTTPException(status_code=503, detail="TTS ist nicht verfügbar.")
        audio = await tts.synthesize(str(payload.get("text", "")))
        if not audio:
            raise HTTPException(status_code=502, detail="Synthese fehlgeschlagen.")
        # Piper gibt RIFF/WAV, ffmpeg konvertiert zu Ogg. Der Media-Type
        # folgt den echten Bytes, nicht der Absicht.
        media = "audio/wav" if audio[:4] == b"RIFF" else "audio/ogg"
        return Response(content=audio, media_type=media)

    return app


async def run() -> None:
    import uvicorn

    s = get_settings()
    config = uvicorn.Config(
        create_app(),
        host=s.web_host,
        port=s.web_port,
        log_level=s.log_level.lower(),
        access_log=False,
        timeout_keep_alive=30,
    )
    server = uvicorn.Server(config)
    log.info("web_start", url=f"http://{s.web_host}:{s.web_port}")
    with contextlib.suppress(Exception):
        await server.serve()

