# Contributing zu ha-hauskosten

Danke, dass du Zeit investieren willst! Bitte lies vor dem ersten Beitrag:

- [`AGENTS.md`](AGENTS.md) — Die Projekt-Grundregeln
- [`docs/ONBOARDING.md`](docs/ONBOARDING.md) — Wo fange ich an?
- [`docs/STANDARDS.md`](docs/STANDARDS.md) — Coding-Standards

## Schnellstart

```bash
git clone https://github.com/TheRealSimon42/ha-hauskosten.git
cd ha-hauskosten

# Virtuelles Environment (empfohlen: uv)
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Pre-Commit-Hooks installieren
pre-commit install

# Tests laufen lassen
pytest
```

## Branches

```
feat/<short-desc>           # neues Feature
fix/<issue-nr>-<desc>       # Bugfix mit Issue-Referenz
refactor/<what>             # Refactoring ohne Verhaltensänderung
docs/<what>                 # Nur Doku-Änderung
test/<what>                 # Nur Test-Ergänzung
chore/<what>                # Build/CI/Tooling
```

## Commit-Messages (Conventional Commits)

```
<type>(<scope>): <short summary>

<optional longer body>

<optional footer>
```

Typen: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`, `style`.

Breaking Changes:

```
feat(config_flow): rename party field "persons" to "personen"

BREAKING CHANGE: Das Feld "persons" heißt jetzt "personen".
Bestehende Subentries werden via Migration (Schema v2) konvertiert.
```

## Pull-Request-Checkliste

- [ ] Getestet in lokaler HA-Dev-Instanz
- [ ] `ruff check && ruff format` grün
- [ ] `mypy --strict` grün
- [ ] `pytest` grün, Coverage nicht gesunken
- [ ] `CHANGELOG.md` unter `[Unreleased]` aktualisiert
- [ ] Relevante Sub-Agent-Dateien aktuell (falls betroffen)
- [ ] Docs aktualisiert (bei neuen User-sichtbaren Features)
- [ ] Breaking Changes dokumentiert

## Code Review

- Alle PRs brauchen mindestens einen Review
- `integration-architect` reviewt bei Änderungen an Datenmodell oder Architektur
- Der Autor mergt selbst nach grünem Review und grüner CI

## Bei Fragen

Lieber ein Issue öffnen als still still im Code kämpfen. Auch simple Fragen sind willkommen.
