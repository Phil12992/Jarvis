-- JARVIS Memory-Schema.
--
-- FTS5 mit unicode61 + remove_diacritics ist hier keine Geschmacksfrage:
-- ohne remove_diacritics findet eine Suche nach "Graf" die Erinnerung
-- "Größe" nicht, und du schreibst auf Telegram alles ohne Umlaute.

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

-- ---------------------------------------------------------------------------
-- Sessions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    channel       TEXT NOT NULL,              -- telegram | web
    peer          TEXT NOT NULL,              -- chat_id oder username
    created_at    INTEGER NOT NULL,
    last_seen_at  INTEGER NOT NULL,
    summary       TEXT DEFAULT ''             -- verdichteter Gelaeuft
);

CREATE INDEX IF NOT EXISTS idx_sessions_last_seen ON sessions(last_seen_at DESC);

-- ---------------------------------------------------------------------------
-- Nachrichten
--
-- role: system | user | assistant | tool
-- Der Transcript bleibt vollstaendig, damit ein Context-Rebuild nach einem
-- Neustart exakt denselben Stand ergibt wie vorher.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    tool_call_id TEXT,
    tool_name   TEXT,
    model       TEXT,
    created_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

-- ---------------------------------------------------------------------------
-- Langzeit-Erinnerungen
--
-- Das ist das eigentliche persistente Gedaechtnis. Wird nicht aus dem
-- Transcript abgeleitet, sondern gezielt geschrieben, wenn der Agent etwas
-- merkt. importance 1..5 steuert, was beim Inject wieder auftaucht.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL DEFAULT 'fact', -- fact | preference | project | person
    content     TEXT NOT NULL,
    source      TEXT DEFAULT '',              -- session_id oder 'user'
    importance  INTEGER NOT NULL DEFAULT 3,
    pinned      INTEGER NOT NULL DEFAULT 0,   -- nie automatisch vergessen
    hits        INTEGER NOT NULL DEFAULT 0,   -- wie oft wurde es benutzt
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(pinned DESC, importance DESC, updated_at DESC);

-- Volltextsuche ueber die Erinnerungen.
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;

-- ---------------------------------------------------------------------------
-- Skills-Status: welche Skills hat der Agent schon geladen und wann
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skill_usage (
    name        TEXT PRIMARY KEY,
    uses        INTEGER NOT NULL DEFAULT 0,
    last_used   INTEGER
);

-- ---------------------------------------------------------------------------
-- KV fuer Kleinigkeiten, die keinen eigenen Platz brauchen
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kv (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  INTEGER NOT NULL
);
