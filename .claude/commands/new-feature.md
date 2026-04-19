---
description: Start a new feature with issue, branch, and stub tests
---

# Neues Feature starten

Führe folgende Schritte durch:

1. **Frage** nach dem Feature-Namen (kurz, kebab-case) und der groben Beschreibung
2. **Prüfe**, ob der Scope in `docs/ARCHITECTURE.md` → "Abgrenzung" passt. Bei Zweifeln: `integration-architect` konsultieren
3. **Lege** ein GitHub-Issue via `gh issue create` an mit Template `feature_request`
4. **Erzeuge** einen Branch `feat/<feature-name>`
5. **Identifiziere**, welche Sub-Agents dieses Feature berühren (Routing-Tabelle in `AGENTS.md`)
6. **Erstelle** Stub-Files gemäß `docs/ONBOARDING.md` Reihenfolge
7. **Schreibe** zuerst einen failing Test, bevor Implementation beginnt (TDD)
8. **Aktualisiere** `CHANGELOG.md` unter `[Unreleased]` → `### Added`

Gib dem User zum Schluss eine klare Status-Zusammenfassung: Issue-Link, Branch-Name, welche Files zu bearbeiten sind, welche Sub-Agents involviert sind.
