<<<<<<< HEAD
# JARVIS — Personal Agent Harness

Ein persönlicher KI-Agent für Homelab-Steuerung, Recherche und Coding, der auf einem Proxmox-Host in einem unprivilegierten LXC-Container läuft.

## Architektur

```
┌─────────────────────────────────────────────────────────────┐
│                    Proxmox Host (ThinkPad)                   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              LXC CT 100 (unprivileged)               │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │   │
│  │  │  Agent Loop │  │   Memory    │  │   Skills    │  │   │
│  │  │  (Python)   │  │  (SQLite)   │  │  (Markdown) │  │   │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  │   │
│  │         │                │                │          │   │
│  │  ┌──────▼────────────────▼────────────────▼──────┐  │   │
│  │  │              Tool Registry                    │  │   │
│  │  │  proxmox_*  |  docker_*  |  code_runner (ext) │  │   │
│  │  └──────────────────────────────────────────────┘  │   │
│  │         │                    │                    │   │
│  │  ┌──────▼──────┐      ┌──────▼──────┐            │   │
│  │  │   Telegram  │      │    Web      │            │   │
│  │  │   Channel   │      │  Channel    │            │   │
│  │  └─────────────┘      └─────────────┘            │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

- **Proxmox Host**: Lenovo ThinkPad, i5-2520M, 7,6 GB RAM, 2C/4T
- **CT**: 3 GB RAM, 1 GB Swap, 2 Cores, 24 GB Disk, Debian 12
- **LLM**: Cloud-Modelle via OpenRouter (Primär + Fallback)
- **STT**: Groq Whisper (Browser-Mic), Telegram nativ (Sprachnachrichten)
- **TTS**: Piper (lokal im CT, optional)

## Quickstart (auf dem Proxmox-Host)

```bash
# 1. SSH-Key erzeugen (falls nicht vorhanden)
ssh-keygen -t ed25519 -C "jarvis@proxmox"
export SSH_PUBKEY="$(cat ~/.ssh/id_ed25519.pub)"

# 2. CT anlegen
cd /path/to/jarvis/deploy
./create-ct.sh

# 3. In den CT wechseln und Bootstrap laufen lassen
pct enter 100
bash /root/bootstrap-ct.sh

# 4. .env befüllen (OpenRouter Key, Telegram Token, Proxmox Token, ...)
cp .env.example .env
# ... editieren ...

# 5. Container bauen und starten
cd /opt/jarvis
docker compose up -d --build
```

## Konfiguration (`.env`)

| Variable | Beschreibung |
|----------|--------------|
| `LLM_PRIMARY` | Primärmodell (z.B. `openrouter/nemotron-3-ultra`) |
| `LLM_FALLBACKS` | Komma-getrennte Fallback-Modelle |
| `OPENROUTER_API_KEY` | OpenRouter API Key |
| `TELEGRAM_BOT_TOKEN` | Von @BotFather |
| `TELEGRAM_ALLOWED_USERS` | Komma-getrennte Telegram User IDs |
| `PROXMOX_HOST` | Hostname/IP des Proxmox (für API) |
| `PROXMOX_USER` | `root@pam` |
| `PROXMOX_TOKEN_ID` | API Token ID (z.B. `jarvis`) |
| `PROXMOX_TOKEN_SECRET` | API Token Secret (UUID) |
| `STT_PROVIDER` | `groq` für Browser-Mic |
| `STT_API_KEY` | Groq API Key |
| `TTS_ENABLED` | `true`/`false` |
| `MAX_TOOL_CALLS` | Budget für Tool-Aufrufe pro Turn (Default: 24) |
| `MAX_TOOL_ROUNDS` | Max. Runden pro Turn (Default: 12) |

## Kanäle

### Telegram
- Textnachrichten → Agent antwortet
- Sprachnachrichten → Telegram transkribiert nativ → Agent antwortet per **Voice** (TTS)
- `/start` — neue Session
- `/tools` — registrierte Tools anzeigen
- `/memory` — gespeicherte Erinnerungen
- `/reset` — Session zurücksetzen (Memory bleibt)

### Web (FastAPI + WebSocket)
- `http://<CT-IP>:8080` — Chat-UI mit Push-to-Talk
- WebSocket `/ws/chat?session_id=...&token=...` — Echtzeit-Chat
- `POST /api/stt` — Audio hochladen → Transkription
- `POST /api/tts` — Text → Audio (Piper)

## Skills

Skills liegen als Markdown-Dateien in `SKILLS_DIR` (`/data/skills`):

```markdown
---
title: Mein Skill
description: Was dieser Skill kann
requires: ["anderer_skill"]
tools: ["proxmox_status"]
---

# Mein Skill

Anweisungen für das Modell, wie dieser Skill genutzt wird.
```

Der Agent lädt den **Index** aller Skills in den System-Prompt. Wenn ein Skill passt, soll das Modell ihn per `skill_read`-Tool (geplant) vollständig laden.

## Tools

| Tool | Tier | Beschreibung |
|------|------|--------------|
| `proxmox_status` | SAFE | Cluster-Übersicht |
| `proxmox_node_health` | SAFE | Node-Details |
| `proxmox_storage` | SAFE | Storage-Pools |
| `proxmox_lxc_logs` | SAFE | Container-Logs lesen |
| `docker_ps` | SAFE | Container im CT listen |
| `docker_logs` | SAFE | Container-Logs lesen |

**Tier-System**:
- `SAFE` — sofort ausführen
- `CONFIRM` — Benutzer muss mit "ja" bestätigen
- `DANGER` — standardmäßig deaktiviert, braucht `allow_danger()` im Code

## Development

```bash
# Test-Venv anlegen
python -m venv .venv-test
.venv-test\Scripts\pip install -e . -q
.venv-test\Scripts\pip install structlog pydantic pydantic-settings pyyaml -q

# Smoke-Tests
.venv-test\Scripts\python scripts\smoke.py
```

## Deployment-Notizen

- **Kein Host-Shell-Zugriff**: Der Agent führt keine Befehle auf dem Proxmox-Host aus. Code-Ausführung läuft über einen separaten Code-Runner (nicht im Scope dieses Repos).
- **Docker im CT**: Der `docker.sock` wird per `docker-compose.yml` in den CT gemountet. Da der CT unprivilegiert ist, gibt das keine Host-Eskalation.
- **Persistenz**: `/data/memory`, `/data/skills`, `/data/audio` als Volumes in `docker-compose.yml` mounten.

## Lizenz

MIT
=======
# JARVIS
>>>>>>> 278aae25655b57be16d4c145db697f4fd918b7fb
