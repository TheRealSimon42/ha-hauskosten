# Onboarding für Agents

> „Wo fange ich an?" — Dieses Dokument antwortet genau das.

## Schritt 0 — Context aufnehmen

Lies in dieser Reihenfolge:

1. `AGENTS.md` — die Projekt-Verfassung
2. `docs/ARCHITECTURE.md` — Systemüberblick
3. `docs/DATA_MODEL.md` — Datenstrukturen
4. `docs/DISTRIBUTION.md` — Verteilungs-Algorithmen (sehr wichtig!)
5. `docs/STANDARDS.md` — Coding-Standards
6. Die `.md`-Datei deines zugewiesenen Sub-Agents in `.claude/agents/`

Erst nach diesen sechs Dokumenten hast du genug Context, um sinnvoll zu arbeiten.

## Schritt 1 — Check den Projekt-Status

```bash
# Was ist gemerged auf main?
git log --oneline -20

# Was ist WIP?
git branch -a

# Offene Issues?
gh issue list
```

## Schritt 2 — Dein erster Task nach Phase

### Phase „Projekt-Setup" (jetzt aktiv)

Die Reihenfolge, in der die ersten Files entstehen sollten:

| # | Datei | Agent | Blocking für |
|---|---|---|---|
| 1 | `custom_components/hauskosten/const.py` | `integration-architect` | alle weiteren |
| 2 | `custom_components/hauskosten/models.py` | `integration-architect` | Storage, Coordinator, Config Flow |
| 3 | `custom_components/hauskosten/manifest.json` | `integration-architect` | HACS-Validation |
| 4 | `custom_components/hauskosten/distribution.py` | `distribution-logic` | Coordinator |
| 5 | `tests/test_distribution.py` | `test-writer` | Merge-Blocker für #4 |
| 6 | `custom_components/hauskosten/calculations.py` | `distribution-logic` | Coordinator |
| 7 | `tests/test_calculations.py` | `test-writer` | Merge-Blocker für #6 |
| 8 | `custom_components/hauskosten/storage.py` | `coordinator-dev` | Config Flow, Coordinator |
| 9 | `custom_components/hauskosten/coordinator.py` | `coordinator-dev` | Sensor |
| 10 | `tests/test_coordinator.py` | `test-writer` | Merge-Blocker für #9 |
| 11 | `custom_components/hauskosten/config_flow.py` | `config-flow-dev` | Setup |
| 12 | `custom_components/hauskosten/strings.json` + `translations/*.json` | `config-flow-dev` | Config Flow UI |
| 13 | `tests/test_config_flow.py` | `test-writer` | Merge-Blocker für #11 |
| 14 | `custom_components/hauskosten/__init__.py` | `integration-architect` | Nichts, aber Last |
| 15 | `custom_components/hauskosten/sensor.py` | `sensor-dev` | Nichts |
| 16 | `tests/test_sensor.py` | `test-writer` | Merge-Blocker für #15 |
| 17 | `README.md` (volle Version) | `docs-writer` | Release |
| 18 | GitHub Workflows | `release-manager` | Release |

**Regel:** Pure-Logic-Module (`distribution`, `calculations`) werden **vor** den HA-abhängigen Modulen gebaut und **getestet**. Das ist der Anker der gesamten Integration.

### Phase „Feature-Arbeit"

Wenn Phase Setup durch ist: `gh issue list --label "good first task"` und ab da pro Task ein Branch.

## Schritt 3 — Arbeitsmodus

### Branch-Naming

```
feat/<short-desc>
fix/<issue-nr>-<short-desc>
docs/<what>
refactor/<what>
```

### Loop

```
1. Create branch from main
2. Write test(s) first (TDD wo möglich)
3. Implement
4. Run: ruff check && ruff format && mypy && pytest
5. Commit (conventional)
6. Push + Open PR
7. CI must pass
8. Self-review, link issue
9. Merge via squash
```

### Was tun, wenn...

**... eine Architektur-Entscheidung fehlt?**
- Keine eigenmächtigen Entscheidungen treffen
- Issue eröffnen, `integration-architect`-Label setzen
- Warten oder `architect`-Agent anrufen

**... ein Test failing-by-design ist?**
- Mit `@pytest.mark.xfail(reason="...", strict=True)` markieren
- Issue dazu eröffnen

**... eine HA-API-Änderung auftritt?**
- `docs/ARCHITECTURE.md` prüfen, ob das Muster noch valid ist
- HA-Core-Changelog lesen (https://www.home-assistant.io/blog/categories/release-notes/)
- Im Zweifel: `integration-architect` konsultieren

## Schritt 4 — Dokumentation aktualisieren

**Bei jeder PR prüfen:**

- [ ] `CHANGELOG.md` — neuer Eintrag unter `[Unreleased]`
- [ ] `docs/*.md` — wenn Struktur-Änderungen, Doku aktualisieren
- [ ] `README.md` — wenn User-sichtbare Änderungen
- [ ] `strings.json` + Translations — bei UI-Änderungen

Eine PR, die `docs/DATA_MODEL.md` widerspricht **ohne dass die Datei mit aktualisiert wird**, wird nicht gemergt.

## Schritt 5 — Release

Nur der `release-manager`-Agent macht Releases. Workflow:

1. Alle geplanten PRs für die Version gemerged
2. `CHANGELOG.md` aufräumen — `[Unreleased]` → `[0.X.0] - YYYY-MM-DD`
3. `manifest.json` Version-Bump
4. Tag: `git tag v0.X.0 && git push --tags`
5. GitHub Release mit Changelog-Auszug
6. Bei Major/Minor: HACS-Default-Repo-PR öffnen (falls noch nicht dort)

## FAQ für Agents

**F: Darf ich eine neue Dependency hinzufügen?**
A: Nur mit sehr gutem Grund. HA-Integrations sollen minimal sein. Wenn, dann in `manifest.json.requirements` und in `pyproject.toml`.

**F: Wie teste ich die Integration lokal?**
A: `homeassistant/setup.py` in HA Dev-Env, oder via `uv pip install -e .` + HA Core lokal. Details in `docs/STANDARDS.md`.

**F: Darf ich Python 3.14 Features nutzen?**
A: Nein, Target ist 3.13 (HA Core 2026.x Floor).

**F: Wie mache ich Breaking Changes am Datenmodell?**
A: `CONF_SCHEMA_VERSION` in `const.py` erhöhen, `async_migrate_entry` in `__init__.py` erweitern, neue Schema-Version in `docs/DATA_MODEL.md` dokumentieren.

**F: Darf ich translations/en.json unvollständig lassen?**
A: Nein. Jede Key in `strings.json` muss in beiden Translation-Files existieren. CI prüft das via hassfest.

## Red Flags

Wenn du eins dieser Muster bei dir selbst bemerkst — **stoppen und nachdenken**:

- „Ich passe den Test an, damit er grün wird" → Nein. Test sagt die Wahrheit, Code ist falsch (meistens).
- „Ich kommentiere `type: ignore` ohne Begründung" → Nein. Immer mit `# reason`.
- „Ich rechne schnell im Entity-Property" → Nein. Properties lesen nur.
- „Ich mache das YAML-only-Feature" → Nein. Integration ist UI-first.
- „Ich ignoriere `mypy --strict`" → Nein. Strict ist Gesetz.
