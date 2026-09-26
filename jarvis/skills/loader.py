"""Skill-Loader.

Ein Skill ist eine Markdown-Datei mit YAML-Frontmatter. Skills sind
versionierbar, mit Git diffbar, und brauchen keinen Container-Build.

Warum progressive Offenlegung: der CT hat 3 GB und das Modell hat ein
kontextfenster. Bei 40 Skills mit je 500 Token im System-Prompt sind das
20k Token Grundrauschen bei jedem Chat. Deshalb sieht das Modell
standardmaessig nur Name und Beschreibung in einer Zeile. Den vollen Text
laesst der Agent erst laden, wenn die Skill wirklich passt.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
import yaml

log = structlog.get_logger("jarvis.skills")


@dataclass(slots=True)
class Skill:
    name: str
    description: str
    path: Path
    body: str = ""
    requires: list[str] = None  # type: ignore[assignment]
    tools: list[str] = None  # type: ignore[assignment]
    mtime: float = 0.0

    def __post_init__(self) -> None:
        if self.requires is None:
            self.requires = []
        if self.tools is None:
            self.tools = []

    def index_line(self) -> str:
        return f"- {self.name}: {self.description}"


class SkillStore:
    def __init__(self, skills_dir: str) -> None:
        self.dir = Path(skills_dir)
        self._skills: dict[str, Skill] = {}

    @property
    def count(self) -> int:
        return len(self._skills)

    def load(self) -> int:
        """Laedt oder neu laedt alle Skills. Gibt die Anzahl zurueck."""
        self._skills.clear()
        if not self.dir.exists():
            log.info("skills_dir_fehlt", pfad=str(self.dir))
            return 0

        for md in sorted(self.dir.rglob("*.md")):
            try:
                skill = self._parse(md)
            except Exception as exc:  # noqa: BLE001 - ein kaputter Skill darf den Rest nicht kippen
                log.warning("skill_unlesbar", datei=md.name, fehler=repr(exc))
                continue
            if skill:
                self._skills[skill.name] = skill

        log.info("skills_geladen", anzahl=len(self._skills), ordner=str(self.dir))
        return len(self._skills)

    def _parse(self, path: Path) -> Skill | None:
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---"):
            return None

        parts = raw.split("---", 2)
        if len(parts) < 3:
            return None

        try:
            meta = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError as exc:
            log.warning("skill_yaml_fehler", datei=path.name, fehler=str(exc))
            return None

        name = str(meta.get("name") or path.stem).strip()
        description = str(meta.get("description") or "").strip()
        if not name or not description:
            log.warning("skill_ohne_name_oder_beschreibung", datei=path.name)
            return None

        requires = meta.get("requires") or []
        tools = meta.get("tools") or []
        return Skill(
            name=name,
            description=description,
            path=path,
            body=parts[2].strip(),
            requires=[str(r) for r in requires],
            tools=[str(t) for t in tools],
            mtime=path.stat().st_mtime,
        )

    # --- Zugriff ------------------------------------------------------------

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return sorted(self._skills)

    def missing_requirements(self, s: Skill) -> list[str]:
        """Skills duerfen sich ueber `requires` gegenseitig bedingen.
        Zirkulaere Abhaengigkeiten werden hier entdeckt, nicht erst beim
        Aufruf zur Laufzeit."""
        seen: set[str] = set()
        stack = list(s.requires)
        missing: list[str] = []
        while stack:
            dep = stack.pop()
            if dep in seen:
                continue
            seen.add(dep)
            child = self._skills.get(dep)
            if child is None:
                missing.append(dep)
            else:
                stack.extend(child.requires)
        return missing

    def index_prompt(self, only: list[str] | None = None) -> str:
        """Der kompakte Index, der immer im System-Prompt steht."""
        pool = [self._skills[n] for n in only if n in self._skills] if only else list(
            self._skills.values()
        )
        if not pool:
            return ""
        lines = [s.index_line() for s in sorted(pool, key=lambda x: x.name)]
        return "\n".join(lines)

    def reload_if_stale(self, max_age_seconds: float = 300.0) -> bool:
        """Prueft ob sich Dateien auf dem Host geaendert haben. Damit werden
        neue Skills ohne Neustart aktiv, wichtig wenn man am Telefon keinen
        Neustart ausloesen will."""
        if not self.dir.exists():
            return False
        now = time.time()
        known = {s.path: s for s in self._skills.values()}
        for md in self.dir.rglob("*.md"):
            if md not in known or known[md].mtime != md.stat().st_mtime:
                return self.reload() > 0
        if self._skills and now - self._last_load > max_age_seconds:
            return False
        return False

    _last_load: float = 0.0

    def reload(self) -> int:
        self._last_load = time.time()
        return self.load()

    def stats(self) -> dict[str, Any]:
        return {
            "anzahl": self.count,
            "namen": self.names(),
            "waechselzeit": self._last_load,
        }


_skill_store: SkillStore | None = None


def get_skill_store() -> SkillStore:
    global _skill_store
    if _skill_store is None:
        from jarvis.config import get_settings

        _skill_store = SkillStore(get_settings().skills_dir)
    return _skill_store
