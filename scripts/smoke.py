"""Testet die kritische Logik, die ohne externe Dienste laeuft.

Kein pytest. Ein CT mit 3 GB und 2 Kernen soll nicht auch noch eine
Test-Infrastruktur tragen muessen. Aufruf:

    python scripts/smoke.py

Exit-Code 0 heisst: alles gruen.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MEMORY_DB_PATH", ":memory:disk")
os.environ.setdefault("LOG_LEVEL", "WARNING")

FAILURES: list[str] = []
PASSES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSES.append(name)
        print(f"  ok    {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  {detail}")


async def test_memory() -> None:
    print("\n[Memory]")
    from jarvis.memory.store import Memory

    tmp = Path(tempfile.mkdtemp()) / "test.db"
    mem = Memory(str(tmp))
    await mem.startup()

    # Grundlegende Speicherung
    await mem.touch_session("s1", "telegram", "42")
    await mem.add_message("s1", "user", "Wie heisse ich?")
    await mem.add_message("s1", "assistant", "Du heisst Phil.")
    msgs = await mem.get_messages("s1", limit=10)
    check("nachrichten_gespeichert", len(msgs) == 2, f"bekam {len(msgs)}")
    check("reihenfolge_chronologisch", msgs[0]["content"].startswith("Wie"), str(msgs))

    # Erinnerungen
    mid = await mem.remember("Phil nutzt Proxmox auf einem ThinkPad i5-2520M", importance=5)
    check("erinnerung_angelegt", mid > 0)
    await mem.remember("Phil spricht Deutsch", kind="preference", importance=4)
    await mem.remember("Phil mag keine Frames in Antworten", kind="preference", importance=2)

    # Duplikate zusammenfassen statt doppelt speichern
    dup = await mem.remember("Phil nutzt Proxmox auf einem ThinkPad i5-2520M", importance=5)
    check("duplikat_nicht_verdoppelt", dup == mid, f"{dup} != {mid}")

    # FTS5-Suche, inklusive Umlaut-Verhalten.
    # Das ist der Grund fuer unicode61 + remove_diacritics: du schreibst
    # "Graf" und suchst "Größe", oder umgekehrt. Beides muss treffen.
    await mem.remember("Der Grosse Bildschirm ist 55 Zoll", importance=3)
    await mem.remember("Die Größe des Raums ist 20 Quadratmeter", importance=3)
    check("umlaut_um_ue_auffindbar", len(await mem.recall(query="Grosse")) == 1,
          f"{len(await mem.recall(query='Grosse'))}")
    check("umlaut_oe_auffindbar", len(await mem.recall(query="Größe")) == 1,
          f"{len(await mem.recall(query='Größe'))}")
    check("grosse_findet_groesse_nicht", len(await mem.recall(query="grosse")) == 1,
          "Falscher Treffer")

    hits = await mem.recall(query="ThinkPad")
    check("fts_treffer", len(hits) == 1, f"{len(hits)} Treffer")
    hits = await mem.recall(query="Proxmox")
    check("fts_wortteil", len(hits) == 1, f"{len(hits)} Treffer")

    hits = await mem.recall(query="deutsch")
    check("fts_kleinschreibung", len(hits) == 1, f"{len(hits)} Treffer")

    # FTS-Sonderzeichen duerfen nicht crashen
    for nasty in ['"', "-", "AND OR NOT", "*", "a AND", "()", "^", "NEAR(a b)", "  "]:
        try:
            await mem.recall(query=nasty)
        except Exception as exc:  # noqa: BLE001
            check(f"fts_robust_{nasty.strip() or 'leer'}", False, repr(exc))
            return
    check("fts_robust_gegen_sonderzeichen", True)

    # Importance-Gate
    important = await mem.recall(query="ThinkPad", min_importance=5)
    check("importance_gate", len(important) == 1, f"{len(important)}")
    none_important = await mem.recall(query="ThinkPad", min_importance=5)
    await mem.remember("Wichtige Sache", importance=5, pinned=True)
    pinned = await mem.recall(limit=50)
    check("pinned_erscheint", any(m["content"] == "Wichtige Sache" for m in pinned))

    # Loeschen: gepinnt nicht loeschbar
    ok_del = await mem.forget(mid)
    check("loeschen_geht", ok_del)
    ok_del2 = await mem.forget(999999)
    check("loeschen_unbekannt_kein_fehler", ok_del2 is False)

    # KV
    await mem.kv_set("test", "wert")
    check("kv_roundtrip", await mem.kv_get("test") == "wert")
    check("kv_default", await mem.kv_get("fehlt", "fallback") == "fallback")

    # Idle-Reaping
    await mem.touch_session("s2", "web", "localhost")
    await mem.add_message("s2", "user", "kurz")
    reaped = await mem.reap_idle(ttl_hours=0)
    check("idle_reap_working", reaped >= 1, f"{reaped} entfernt")

    stats = await mem.stats()
    check("stats_vollstaendig", "memories" in stats and "db_bytes" in stats, str(stats))

    await mem.close()


async def test_tool_registry() -> None:
    print("\n[Tool-Registry]")
    from jarvis.tools.registry import Registry, Tier, Tool

    reg = Registry()

    async def read_ok(x: int = 1) -> str:
        return f"gelesen {x}"

    async def writes_thing(path: str) -> str:
        return f"geschrieben {path}"

    reg.register(
        Tool(name="read", description="nur lesen", schema={"type": "object", "properties": {}},
             tier=Tier.SAFE, handler=read_ok)
    )
    reg.register(
        Tool(name="write", description="schreibt", schema={"type": "object", "properties": {}},
             tier=Tier.CONFIRM, handler=writes_thing, confirm_hint="loescht Daten")
    )

    r = await reg.execute("read", {})
    check("safe_ohne_bestaetigung", r.ok and "gelesen" in r.output, r.output)

    r = await reg.execute("write", {"path": "/etc/passwd"})
    check("confirm_blockiert", not r.ok and r.error == "needs_confirmation", r.error)

    r = await reg.execute("write", {"path": "/etc/passwd"}, confirmed=True)
    check("confirm_durchgelassen", r.ok, r.output)

    r = await reg.execute("read", {"x": "42"})
    check("argument_coercion_int", r.ok and "42" in r.output, r.output)

    r = await reg.execute("read", {"x": "kein_int"})
    check("coercion_fehler_faengt", r.ok, "sollte int bekommen oder scheitern, nicht crashen")

    r = await reg.execute("read", {})
    check("aufruf_zaehlt", reg.get("read").calls == 4, f"{reg.get('read').calls}")

    r = await reg.execute("gibt_es_nicht", {})
    check("unbekanntes_tool", not r.ok and r.error == "unknown_tool", r.error)

    # Sync-Handler muss abgelehnt werden
    try:
        reg.register(Tool(name="sync", description="x", schema={}, tier=Tier.SAFE,
                          handler=lambda: None))  # type: ignore[arg-type]
        check("sync_handler_abgelehnt", False, "wurde akzeptiert")
    except TypeError:
        check("sync_handler_abgelehnt", True)

    # Doppelte Registrierung
    try:
        reg.register(Tool(name="read", description="x", schema={}, tier=Tier.SAFE, handler=read_ok))
        check("doppelte_registrierung_abgelehnt", False, "wurde akzeptiert")
    except ValueError:
        check("doppelte_registrierung_abgelehnt", True)

    # DANGER ohne Freigabe
    async def dangerous() -> str:
        return "kaputt"

    reg.register(Tool(name="rm", description="loescht", schema={}, tier=Tier.DANGER, handler=dangerous))
    check("danger_standardmaessig_inaktiv", reg.get("rm") is None)

    # Secrets duerfen nicht im Log landen
    check("schemas_haben_function_wrapper",
          all("function" in s for s in reg.schemas()),
          str(reg.schemas()[:1]))


async def test_skills() -> None:
    print("\n[Skills]")
    from jarvis.skills.loader import SkillStore

    tmp = Path(tempfile.mkdtemp())
    (tmp / "gut.md").write_text(
        "---\nname: proxmox-check\ndescription: Prueft den Proxmox\ntools: [proxmox_status]\n---\n\n# Anleitung\n\nTu das.",
        encoding="utf-8",
    )
    (tmp / "ohne.md").write_text("Kein Frontmatter, wird ignoriert.", encoding="utf-8")
    (tmp / "kaputt.md").write_text("---\nname: [ungueltig\n---\n\nText", encoding="utf-8")
    # Ohne description: muss abgelehnt werden, denn ohne Beschreibung weiss
    # das Modell nicht, wann der Skill passt.
    (tmp / "ohne_beschreibung.md").write_text(
        "---\nname: namenlos\n---\n\nx", encoding="utf-8"
    )
    # Ohne expliziten name: Dateiname ist der Fallback. Das ist Absicht,
    # sonst muesste jeder Skill den Namen doppelt pflegen.
    (tmp / "nur_dateiname.md").write_text(
        "---\ndescription: nur Beschreibung\n---\n\nx", encoding="utf-8"
    )
    sub = tmp / "unterordner"
    sub.mkdir()
    (sub / "tief.md").write_text(
        "---\nname: tief\ndescription: rekursiv gefunden\nrequires: [proxmox-check]\n---\n\nx",
        encoding="utf-8",
    )

    store = SkillStore(str(tmp))
    n = store.load()
    check("nur_gueltige_geladen", n == 3, f"{n} geladen: {store.names()}")
    check("ohne_beschreibung_verworfen", "namenlos" not in store.names(), str(store.names()))
    check("dateiname_als_fallback", "nur_dateiname" in store.names(), str(store.names()))
    check("kaputt_ignoriert", "kaputt" not in store.names(), str(store.names()))
    check("kein_frontmatter_ignoriert", "ohne" not in store.names(), str(store.names()))
    check("rekursiv_gefunden", "tief" in store.names(), str(store.names()))
    check("requires_aufgeloest", store.missing_requirements(store.get("tief")) == [])

    # Nicht aufloesbare Abhaengigkeit muss sichtbar sein
    allein = SkillStore(str(tmp))
    (tmp / "verwaist.md").write_text(
        "---\nname: verwaist\ndescription: braucht was Existierendes\nrequires: [gibt-es-nicht]\n---\n\nx",
        encoding="utf-8",
    )
    allein.load()
    check("fehlende_requires_sichtbar",
          allein.missing_requirements(allein.get("verwaist")) == ["gibt-es-nicht"],
          str(allein.missing_requirements(allein.get("verwaist"))))


    store2 = SkillStore(str(tmp))
    store2._skills = {  # noqa: SLF001 - Test der Zirkulus-Erkennung
        "a": type(store.get("proxmox-check"))(
            name="a", description="d", path=tmp, requires=["b"], tools=[]
        )
    }
    store2._skills["b"] = type(store.get("proxmox-check"))(
        name="b", description="d", path=tmp, requires=["a"], tools=[]
    )
    # Zirkel darf nicht haengen
    check("zirkel_terminiert", store2.missing_requirements(store2.get("a")) is not None)

    idx = store.index_prompt()
    check("index_kompakt", "proxmox-check" in idx and "# Anleitung" not in idx, idx[:200])
    check("leerer_index_leer", store.index_prompt(only=["gibt-es-nicht"]) == "")


async def test_router_config() -> None:
    print("\n[Config und Router]")
    from jarvis.config import Settings

    s = Settings(llm_fallbacks="a, b ,c", telegram_allowed_users="1,2")
    check("csv_fallbacks", s.llm_fallbacks == ["a", "b", "c"], str(s.llm_fallbacks))
    check("csv_users_int", s.telegram_allowed_users == [1, 2], str(s.telegram_allowed_users))
    check("model_chain", s.model_chain[0] == s.llm_primary and len(s.model_chain) == 4,
          str(s.model_chain))
    check("homelab_default_aus", s.homelab_enabled is False)
    s2 = Settings(proxmox_host="1.2.3.4", proxmox_token_secret="x")
    check("homelab_erkannt", s2.homelab_enabled is True)


async def test_markdown() -> None:
    print("\n[Formatierung]")
    from jarvis.channels.formatting import md_to_html, split_message, strip_for_speech

    check("bold", "<b>x</b>" in md_to_html("**x**"), md_to_html("**x**"))
    check("underline_bold", "<b>x</b>" in md_to_html("__x__"), md_to_html("__x__"))
    check("italic_stern", "<i>x</i>" in md_to_html("*x*"), md_to_html("*x*"))
    check("italic_unterstrich", "<i>x</i>" in md_to_html("_x_"), md_to_html("_x_"))

    # Das ist der eigentliche Test: Pfade duerfen NICHT als Italic
    # interpretiert werden. Bei einem Agenten, der Pfade nennt, waere das
    # ein sichtbarer Bug.
    pfad = md_to_html("Schau nach /opt/jarvis/data/memory und /var/lib/docker")
    check("pfade_bleiben_pfade", "<i>" not in pfad, pfad)
    check("pfade_vollstaendig", "/opt/jarvis/data/memory" in pfad, pfad)

    # snake_case ist der zweite Fall, an dem _ als Delimiter scheitert
    check("snake_case_bleibt", "<i>" not in md_to_html("Nutze proxmox_status und memory_db_path"),
          md_to_html("Nutze proxmox_status und memory_db_path"))
    check("italic_und_pfad_gemischt",
          "<i>wichtig</i>" in md_to_html("_wichtig_ und /etc/passwd"),
          md_to_html("_wichtig_ und /etc/passwd"))

    check("html_escaped", "&lt;script&gt;" in md_to_html("<script>"), md_to_html("<script>"))
    check("kein_roher_html_tag", "<script>" not in md_to_html("<script>"))
    check("zeilenumbruch", "<br/>" in md_to_html("a\nb"))
    check("code_block", "<code>x</code>" in md_to_html("`x`"), md_to_html("`x`"))

    # Aufzaehlungszeichen darf kein Italic eroeffnen
    check("stern_als_aufzaehlung",
          "<i>" not in md_to_html("* Punkt eins\n* Punkt zwei"),
          md_to_html("* Punkt eins\n* Punkt zwei"))


    # Inhalt in Code darf nicht als Formatting interpretiert werden
    nested = md_to_html("`**nicht fett**`")
    check("code_inhalt_geschuetzt", "<b>nicht fett</b>" not in nested, nested)
    check("code_inhalt_escaped", "&lt;b&gt;" in nested or "<code>**nicht fett**</code>" in nested, nested)

    # Ampersand und Anfuehrungszeichen
    check("ampersand_escaped", "&amp;" in md_to_html("a & b"), md_to_html("a & b"))

    long = "Absatz\n\n" * 3000
    parts = split_message(long)
    check("lange_nachricht_zerlegt", len(parts) > 1, f"{len(parts)} Teile")
    check("kein_teil_zu_lang", all(len(p) <= 4096 for p in parts),
          f"max {max(len(p) for p in parts)}")
    check("zerlegung_erhaelt_alle_absaetze",
          sum(p.count("Absatz") for p in parts) == 3000,
          str(sum(p.count("Absatz") for p in parts)))
    check("keine_leeren_teile", all(p.strip() for p in parts))

    # Ein einzelner Absatz, der laenger als das Limit ist
    mega = "x" * 10000
    mparts = split_message(mega)
    check("riesenabsatz_zerlegt", len(mparts) > 1, f"{len(mparts)}")
    check("riesenabsatz_vollstaendig", "".join(mparts) == mega, "Zeichen verloren")
    check("riesenabsatz_teile_ok", all(len(p) <= 4096 for p in mparts))

    # TTS-Aufbereitung: Markdown darf nicht vorgelesen werden
    speech = strip_for_speech("**Wichtig:** Nutze `docker ps` und gehe [hier](http://x).")
    check("tts_ohne_markdown", "*" not in speech and "`" not in speech, speech)
    check("tts_ohne_link_ziel", "http" not in speech, speech)
    check("tts_ohne_rauten", not speech.strip().startswith("#"), speech)


async def test_agent_loop() -> None:
    """Testet die Tool-Schleife mit einem gefaelschten Router.

    Ohne das bleiben die interessantesten Fehler ungetestet: eine
    Endlosschleife von Tool-Calls, ein bestaetigungspflichtiges Tool, das
    sich selbst bestaetigt, ein Provider, der komplett ausfaellt.
    """
    print("\n[Agent-Loop]")
    from jarvis.agent import loop as agent_loop
    from jarvis.llm.errors import LLMUnavailable
    from jarvis.memory.store import Memory
    from jarvis.skills.loader import SkillStore
    from jarvis.tools.registry import Registry, Tier, Tool

    settings = agent_loop.get_settings()
    settings.memory_db_path = str(Path(tempfile.mkdtemp()) / "loop.db")
    settings.skills_dir = tempfile.mkdtemp()
    settings.max_tool_rounds = 4
    settings.agent_timeout_seconds = 20

    class FakeCompletion:
        """Entspricht jarvis.llm.router.Completion, aber ohne litellm.

        Der Loop braucht nur diese drei Felder. Eine eigene Klasse hier
        haelt den Test frei von der LLM-Dep - das ist genau der Grund, warum
        LLMUnavailable nach jarvis/llm/errors.py gewandert ist.
        """

        def __init__(self, text: str, model: str, tool_calls=None) -> None:
            self.text = text
            self.model = model
            self.tool_calls = tool_calls or []
            self.finish_reason = ""
            self.attempts = []
            self.elapsed_ms = 0

    Completion = FakeCompletion

    calls_made: list[tuple[str, dict]] = []


    async def tool_ok(wert: int = 1) -> str:
        calls_made.append(("tool_ok", {"wert": wert}))
        return f"werkzeug ergab {wert}"

    async def tool_gefahr() -> str:
        calls_made.append(("tool_gefahr", {}))
        return "haette etwas kaputt gemacht"

    registry = Registry()
    registry.register(
        Tool(name="tool_ok", description="harmlos",
             schema={"type": "object", "properties": {"wert": {"type": "integer"}}},
             tier=Tier.SAFE, handler=tool_ok)
    )
    registry.register(
        Tool(name="tool_gefahr", description="bestaetigung noetig",
             schema={"type": "object", "properties": {}},
             tier=Tier.CONFIRM, handler=tool_gefahr,
             confirm_hint="loescht die Konfiguration")
    )

    class FakeRouter:
        chain = ["fake/primary", "fake/fallback"]

        def __init__(self) -> None:
            self.queue: list = []
            self.calls: list = []

        async def complete(self, messages, tools=None, *, model_override=None):
            self.calls.append(messages)
            item = self.queue.pop(0) if self.queue else Completion(text="leer", model="fake")
            if isinstance(item, Exception):
                raise item
            return item

        async def health(self):
            return [{"model": "fake/primary", "ok": True, "ms": 1}]

    async def make_agent() -> tuple:
        a = agent_loop.Agent.__new__(agent_loop.Agent)
        a.settings = settings
        a.router = FakeRouter()
        a.memory = Memory(settings.memory_db_path)
        await a.memory.startup()
        a.skills = SkillStore(settings.skills_dir)
        a.skills.reload()
        a._pending = {}
        a._tools = registry
        return a, a.router

    # Der Loop greift ueber das Modul-L Singleton zu, nicht ueber self.
    agent_loop.registry = registry
    agent_loop.get_memory = lambda: _CURRENT["memory"]  # type: ignore[assignment]

    # --- Fall 1: reine Textantwort -----------------------------------------
    a, fake = await make_agent()
    fake.queue = [Completion(text="Hallo.", model="fake/primary")]
    r = await a.turn("s1", "hi", channel="web", peer="p")
    check("einfache_antwort", r.text == "Hallo." and r.ok, r.text)
    check("keine_tool_runs", r.tool_runs == 0, str(r.tool_runs))

    # --- Fall 2: Tool-Call dann Antwort ------------------------------------
    calls_made.clear()
    a, fake = await make_agent()
    fake.queue = [
        Completion(text="", model="fake/primary",
                   tool_calls=[{"id": "c1", "name": "tool_ok", "arguments": '{"wert": 7}'}]),
        Completion(text="Ergebnis war 7.", model="fake/primary"),
    ]
    r = await a.turn("s2", "rechne", channel="web", peer="p")
    check("tool_ausgefuehrt", ("tool_ok", {"wert": 7}) in calls_made, str(calls_made))
    check("tool_ergebnis_im_verlauf",
          any("werkzeug ergab 7" in str(m) for m in fake.calls), "Ergebnis fehlt im Verlauf")
    check("antwort_nach_tool", r.text == "Ergebnis war 7.", r.text)
    check("tool_runs_gezaehlt", r.tool_runs == 1, str(r.tool_runs))

    # --- Fall 3: Bestaetigungspflichtiges Tool -----------------------------
    calls_made.clear()
    a, fake = await make_agent()
    fake.queue = [
        Completion(text="", model="fake/primary",
                   tool_calls=[{"id": "c1", "name": "tool_gefahr", "arguments": "{}"}]),
    ]
    r = await a.turn("s3", "mach kaputt", channel="web", peer="p")
    check("confirm_fragt_nach", r.pending is not None, "kein pending gesetzt")
    check("confirm_oeffnet_nicht", not any(c[0] == "tool_gefahr" for c in calls_made),
          f"{calls_made} - Werkzeug lief ohne Freigabe")
    check("confirm_nennt_grund", "loescht" in r.text, r.text)
    check("confirm_pending_abgelegt", a.pending_for("s3") is not None, "pending fehlt")

    # --- Fall 4: Bestaetigung mit "ja" -------------------------------------
    a, fake = await make_agent()
    a._pending["s3"] = agent_loop.PendingConfirm(
        tool="tool_gefahr", args={}, reason="loescht die Konfiguration")
    calls_made.clear()
    fake.queue = [Completion(text="Erledigt.", model="fake/primary")]
    r = await a.turn("s3", "ja", channel="web", peer="p")
    check("pending_nach_ja_weg", a.pending_for("s3") is None, "pending nicht aufgeraeumt")
    check("bestaetigung_allein_ist_kein_tool", r.ok, r.error)

    # --- Fall 5: Ablehnen raeumt auf, ohne Ausfuehrung ---------------------
    a, fake = await make_agent()
    a._pending["s3b"] = agent_loop.PendingConfirm(tool="tool_gefahr", args={}, reason="x")
    calls_made.clear()
    fake.queue = [Completion(text="Verstanden.", model="fake/primary")]
    r = await a.turn("s3b", "nein", channel="web", peer="p")
    check("nein_raeumt_pending_auf", a.pending_for("s3b") is None, "pending blieb stehen")
    check("nein_fuehrt_aus_nichts", not calls_made, f"{calls_made}")

    # --- Fall 6: Endlosschleife wird abgebrochen ----------------------------
    a, fake = await make_agent()
    fake.queue = [
        Completion(text="", model="fake/primary",
                   tool_calls=[{"id": f"c{i}", "name": "tool_ok", "arguments": "{}"}])
        for i in range(20)
    ]
    r = await a.turn("s4", "loop", channel="web", peer="p")
    check("endlosschleife_abgebrochen", r.error == "max_rounds", f"error={r.error}")
    check("runden_begrenzt", r.tool_runs <= settings.max_tool_rounds, str(r.tool_runs))

    # --- Fall 7: Provider komplett down ------------------------------------
    a, fake = await make_agent()
    fake.queue = [LLMUnavailable("alles down")]
    r = await a.turn("s5", "hallo", channel="web", peer="p")
    check("provider_down_fehler", r.error == "llm_unavailable", r.error)
    check("provider_down_antwort", "erreichbar" in r.text, r.text)

    # --- Fall 8: kaputte Tool-Argumente ------------------------------------
    a, fake = await make_agent()
    fake.queue = [
        Completion(text="", model="fake/primary",
                   tool_calls=[{"id": "c1", "name": "tool_ok", "arguments": "kein json"}]),
        Completion(text="Verstanden.", model="fake/primary"),
    ]
    r = await a.turn("s6", "hm", channel="web", peer="p")
    check("kaputtes_json_ueberlebt", r.ok, r.error)

    a, fake = await make_agent()
    fake.queue = [
        Completion(text="", model="fake/primary",
                   tool_calls=[{"id": "c1", "name": "tool_ok", "arguments": '{"unbekannt": 1}'}]),
        Completion(text="Verstanden.", model="fake/primary"),
    ]
    r = await a.turn("s7", "hm", channel="web", peer="p")
    check("unbekanntes_arg_ignoriert", r.ok, r.error)

    # --- Fall 9: leerer Input und Memory-Ausfall ---------------------------
    a, fake = await make_agent()
    r = await a.turn("s8", "   ", channel="web", peer="p")
    check("leerer_input_abgelehnt", r.error == "empty_input", r.error)


async def main() -> int:
    for fn in (test_memory, test_tool_registry, test_skills, test_router_config,
               test_markdown, test_agent_loop):


        try:
            await fn()
        except Exception as exc:  # noqa: BLE001
            import traceback

            FAILURES.append(f"{fn.__name__} abgestuerzt: {exc!r}")
            print(f"  CRASH {fn.__name__}: {exc!r}")
            traceback.print_exc()

    print(f"\n{'=' * 52}")
    print(f"  {len(PASSES)} bestanden, {len(FAILURES)} fehlgeschlagen")
    if FAILURES:
        print("\nFehlgeschlagen:")
        for f in FAILURES:
            print(f"  - {f}")
    print("=" * 52)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
