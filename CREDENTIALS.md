# JARVIS — Vollständige Credentials & API-Liste

Stand: 2026-09-26
Ziel: Alles an einem Ort dokumentieren, damit .env vollständig befüllt werden kann.

---

## 1. Kern (Pflicht)

| Variable | Quelle | Beispiel | Wo eintragen |
|----------|--------|----------|--------------|
| `OPENROUTER_API_KEY` | https://openrouter.ai/keys | `sk-or-v1-abc123...` | `.env` |
| `TELEGRAM_BOT_TOKEN` | @BotFather → `/newbot` | `123456789:ABCdefGHI...` | `.env` |
| `TELEGRAM_ALLOWED_USERS` | @userinfobot → deine ID | `123456789` (komma-getrennt für mehrere) | `.env` |
| `PROXMOX_HOST` | IP/Hostname deines Proxmox | `192.168.178.71` oder `pve` | `.env` |
| `PROXMOX_TOKEN_ID` | frei wählbar, z.B. `jarvis` | `jarvis` | `.env` |
| `PROXMOX_TOKEN_SECRET` | `pveum user token add root@pam jarvis -privsep 0` → UUID | `aaaa-bbbb-cccc-dddd` | `.env` |

---

## 2. Sprache (STT / TTS)

| Variable | Quelle | Beispiel | Wo eintragen |
|----------|--------|----------|--------------|
| `STT_PROVIDER` | `groq` (für Browser-Mic) | `groq` | `.env` |
| `STT_API_KEY` | https://console.groq.com/keys | `gsk_abc123...` | `.env` |
| `STT_MODEL` | Groq Models | `whisper-large-v3-turbo` | `.env` |
| `TTS_ENABLED` | lokal Piper | `true` / `false` | `.env` |
| `TTS_VOICE` | Piper Voice Name | `de_DE-thorsten-medium` | `.env` |

> **Hinweis**: Telegram-Sprachnachrichten werden **von Telegram selbst** transkribiert (kostenlos, kein Key nötig). `STT_*` ist nur für das **Browser-Push-to-Talk-Mic** in der Web-UI.

---

## 3. Home Assistant (optional, aber gewünscht)

| Variable | Quelle | Beispiel | Wo eintragen |
|----------|--------|----------|--------------|
| `HA_URL` | HA-Instanz URL | `http://192.168.178.XX:8123` | `.env` |
| `HA_TOKEN` | HA → Profil → Langelebige Zugriffstoken | `eyJhbGciOiJIUzI1NiIs...` | `.env` |

**In JARVIS nutzen**: Neuer Skill `home_assistant.md` + Tools `ha_call_service`, `ha_get_state`, `ha_get_entities`.

---

## 4. Node-RED (optional)

| Variable | Quelle | Beispiel | Wo eintragen |
|----------|--------|----------|--------------|
| `NODERED_URL` | Node-RED Instanz | `http://192.168.178.XX:1880` | `.env` |
| `NODERED_USER` | Falls Auth aktiv | `admin` | `.env` |
| `NODERED_PASS` | Falls Auth aktiv | `secret` | `.env` |

**In JARVIS nutzen**: Skill `nodered.md` + Tools `nodered_trigger_flow`, `nodered_get_status`.

---

## 5. ECC Skills (aus deinem ECC-Repo)

ECC-Skills liegen in `~/.config/opencode/skills/` oder im Projekt unter `data/skills/`.

**Wichtige Skills für JARVIS**:

| Skill | Zweck | Benötigt |
|-------|-------|----------|
| `agent-architecture-audit` | Agent-Stack prüfen | – |
| `codebase-onboarding` | Repo verstehen | – |
| `deployment-patterns` | Docker/CI/CD | – |
| `docker-patterns` | Dockerfile/Compose | – |
| `github-ops` | PRs, Issues, Releases | `GITHUB_TOKEN` |
| `git-workflow` | Branching, Commits | – |
| `security-review` | Code-Sicherheit | – |
| `tdd-workflow` | Test-Driven Development | – |
| `verification-loop` | Arbeit verifizieren | – |
| `continuous-agent-loop` | Autonome Loops | – |
| `skill-scout` | Skills finden | – |

**Für GitHub-Tools**: `GITHUB_TOKEN` (Personal Access Token, `repo` + `workflow` Scopes) in `.env` eintragen.

---

## 6. Weitere nützliche Integrationen

| Dienst | Variablen | Skill/Tool |
|--------|-----------|------------|
| **GitLab** | `GITLAB_URL`, `GITLAB_TOKEN` | `gitlab-ops` (falls gewünscht) |
| **InfluxDB** | `INFLUX_URL`, `INFLUX_TOKEN`, `INFLUX_ORG`, `INFLUX_BUCKET` | Metriken/Logging |
| **Grafana** | `GRAFANA_URL`, `GRAFANA_API_KEY` | Dashboards |
| **MQTT** | `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASS` | IoT, Home Assistant Bridge |
| **S3/MinIO** | `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` | Backups, Artefakte |
| **PostgreSQL** | `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE` | Persistenz, Analytics |

---

## 7. .env-Vorlage (kopieren & ausfüllen)

```bash
# === KERN ============================================================
OPENROUTER_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USERS=
PROXMOX_HOST=192.168.178.71
PROXMOX_TOKEN_ID=jarvis
PROXMOX_TOKEN_SECRET=

# === SPRACHE =========================================================
STT_PROVIDER=groq
STT_API_KEY=
STT_MODEL=whisper-large-v3-turbo
TTS_ENABLED=true
TTS_VOICE=de_DE-thorsten-medium

# === HOME ASSISTANT ==================================================
HA_URL=
HA_TOKEN=

# === NODE-RED ========================================================
NODERED_URL=
NODERED_USER=
NODERED_PASS=

# === GITHUB (für ECC Skills) =========================================
GITHUB_TOKEN=

# === WEITERE =========================================================
# INFLUX_URL=
# INFLUX_TOKEN=
# MQTT_HOST=
# etc.
```

---

## 8. Nächste Schritte (TODO abarbeiten)

1. **`.env` vollständig befüllen** (oben 6 Pflichtwerte + HA/Node-RED falls gewünscht)
2. **CT neu bauen**: `cd /opt/jarvis && docker compose up -d --build`
3. **Logs prüfen**: `docker logs -f jarvis` — welche Fehler kommen?
4. **Web-UI testen**: `http://192.168.1.100:2993`
5. **Telegram testen**: `/start` an Bot
6. **Home Assistant Skill anlegen** (`data/skills/home_assistant.md`)
7. **Node-RED Skill anlegen** (`data/skills/nodered.md`)
8. **ECC-Skills in `data/skills/` verlinken/kopieren**
9. **Proxmox-Tools testen**: im Chat `proxmox_status` aufrufen

---

## 9. Debug-Checkliste (warum "nichts funktioniert")

| Symptom | Wahrscheinliche Ursache | Fix |
|---------|------------------------|-----|
| Telegram antwortet nicht | `TELEGRAM_BOT_TOKEN` falsch/leer | Token prüfen, Bot angeschrieben? |
| Web-UI lädt, aber Chat sendet nichts | `WEB_PORT` mismatch, CORS, Token | Port 2993 prüfen, `WEB_AUTH_TOKEN` leer lassen |
| Proxmox-Tools geben "nicht konfiguriert" | `PROXMOX_HOST/TOKEN_SECRET` fehlen | `.env` prüfen, CT neu starten |
| STT im Browser geht nicht | `STT_API_KEY` (Groq) fehlt | Key eintragen, `STT_PROVIDER=groq` |
| TTS keine Stimme | Piper Voice nicht im CT | `docker exec -it jarvis ls /data/audio/voices/` |
| Skills werden nicht geladen | `SKILLS_DIR` falsch, Dateien fehlen | `ls /opt/jarvis/data/skills/` prüfen |

---

**Datei speichern als**: `CREDENTIALS.md` im Projekt-Root (`/opt/jarvis/CREDENTIALS.md`).