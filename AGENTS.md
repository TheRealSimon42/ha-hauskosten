# ha-hauskosten — Agent Entry Point

> **Hinweis:** Dies ist der zentrale Einstiegspunkt für alle KI-Coding-Agents (Claude Code, Codex, Cursor, ...). Wenn du ein Agent bist: **Lies diese Datei zuerst vollständig**, dann `docs/ONBOARDING.md` für deinen Task, dann spezifische Referenzen je nach Aufgabe.

`CLAUDE.md` ist ein Symlink auf diese Datei.

---

## Projekt in einem Satz

Eine Home-Assistant-Custom-Integration (HACS-kompatibel), die **Hauskosten in Mehrfamilienhäusern** auf Parteien verteilt — nach Quadratmetern, Personenzahl, Verbrauch oder gleich, für Fixkosten (Versicherung, Müll) und Verbrauchskosten (Wasser, Strom).

## Projekt-Status

**Phase:** Architektur steht, Implementation nicht begonnen.
**Nächster Schritt:** Siehe `docs/ONBOARDING.md` → *„Wo fange ich an?"*

## Was du als Agent wissen musst

1. **Sprache der Integration-UI ist Deutsch**, Code-Kommentare auf Englisch, Commit-Messages auf Englisch (Conventional Commits).
2. **Python 3.13+** (aktuelle HA Core Requirement, Stand 2026).
3. Diese Integration hat **keine externe API** — alle Daten kommen aus dem User-Input via Config Flow und aus HA-Entities (Verbrauchszähler).
4. **Keine YAML-Config-Unterstützung.** Alles via UI (Config Flow + Subentries).
5. **Quality Scale Ziel:** Silver beim ersten Release, Gold innerhalb von sechs Monaten.
6. Diese Integration ist für **private, nicht-kommerzielle Nutzung** lizenziert (CC BY-NC-SA 4.0). Siehe `LICENSE`.

## Architektur in 30 Sekunden

```
User Input (Config Flow Subentries)
        │
        ▼
  Storage API (Parteien + Kostenpositionen)
        │
        ▼
┌───────────────────────────────────────┐
│ DataUpdateCoordinator                 │
│  - Liest Verbrauchs-Entities          │
│  - Ruft distribution.py + calculations│
│  - Erzeugt strukturierte Daten        │
└──────────────┬────────────────────────┘
               │
               ▼
     Sensor-Platform (dynamisch)
               │
               ▼
       Dashboard / Automations
```

Details: **`docs/ARCHITECTURE.md`**

## Sub-Agents — Wer macht was?

Jeder Sub-Agent hat einen klar abgegrenzten Scope. Liste vollständig in `.claude/agents/`.

| Agent | Invoke wann | Primäre Files |
|---|---|---|
| `integration-architect` | Datenmodell- oder Scope-Fragen, neue Features grob einordnen | `docs/*.md`, `models.py` |
| `config-flow-dev` | UI / Config Flow / Subentries / Translations | `config_flow.py`, `translations/*.json`, `strings.json` |
| `coordinator-dev` | Datenaggregation, Storage, Update-Zyklen | `coordinator.py`, `storage.py` |
| `sensor-dev` | Entity-Erzeugung, Attribute, Naming | `sensor.py` |
| `distribution-logic` | Verteilungs-Algorithmen (pure Python) | `distribution.py`, `calculations.py` |
| `test-writer` | pytest-Tests, Fixtures, Edge Cases | `tests/*` |
| `docs-writer` | README, CHANGELOG, Doku-Dateien | `README.md`, `docs/*.md`, `CHANGELOG.md` |
| `release-manager` | Version-Bumps, Tags, Releases | `manifest.json`, `hacs.json`, Workflows |

**Routing-Regel:** Wenn ein Task mehrere Agents berührt, startet der `integration-architect`, delegiert sequenziell, und stellt am Ende Konsistenz sicher.

## Coding Standards (kurz)

Vollständig in `docs/STANDARDS.md`.

- **Formatter:** `ruff format` (Line-Length 88)
- **Linter:** `ruff check` (alle Regeln aus `pyproject.toml`)
- **Type-Checker:** `mypy --strict` auf `custom_components/hauskosten/`
- **Import-Order:** ruff/isort-Style, stdlib → third-party → homeassistant → local
- **Docstrings:** Google-Style, für alle öffentlichen Klassen + Funktionen
- **Editor:** `.editorconfig` gilt (UTF-8, LF, 4 Spaces für Python, 2 für YAML/JSON)
- **Quality Gate:** Codacy-Grade A oder B ist Pflicht, keine Regressionen
- **Commits:** Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`)
- **PRs:** Min. ein Test für neue Logik, `CHANGELOG.md`-Eintrag, grüne CI

## Hard Constraints

Diese Punkte sind nicht verhandelbar. Ein Agent, der dagegen verstößt, macht einen Fehler.

1. **Keine verbrauchten Entities reloggen** — wir lesen Zähler-Entities read-only.
2. **Keine Automationen generieren** — diese Integration erzeugt ausschließlich Sensoren.
3. **Keine I/O im Coordinator-Update-Callback** außer Entity-State-Reads. Datei-I/O läuft über die Storage API.
4. **Keine `async`-Funktion ohne `_LOGGER`-Fehlerpfad.** Unbehandelte Exceptions gehören in Home Assistant Repairs.
5. **Kein `device_id` in der Integration.** Wir referenzieren Verbrauchs-Entities per `entity_id`.
6. **Keine rechtlich-bindende Abrechnung generieren.** Wir tracken Kosten, wir rechnen nicht ab (BetrKV / HeizkostenV sind out-of-scope).
7. **Alle User-Facing-Strings via Translations.** Kein hardcoded Deutsch in Python-Code außer Log-Messages.

## Woher kommt die Wahrheit?

| Frage | Quelle |
|---|---|
| Was soll die Integration können? | Dieses Dokument + `docs/ARCHITECTURE.md` |
| Wie sind Daten strukturiert? | `docs/DATA_MODEL.md` |
| Wie wird verteilt? | `docs/DISTRIBUTION.md` |
| Wie ist der Code zu formatieren? | `docs/STANDARDS.md` + `pyproject.toml` |
| Wo fange ich an? | `docs/ONBOARDING.md` |
| HA-API-Details | <https://developers.home-assistant.io/> |
| HACS-Requirements | <https://hacs.xyz/docs/publish/integration/> |

## Änderungen an diesem Dokument

`AGENTS.md` ist die „Verfassung" des Projekts. Änderungen hier:

1. Nur via PR, nie direkt auf `main`.
2. PR-Beschreibung muss die Motivation nennen.
3. `integration-architect` muss reviewen.
