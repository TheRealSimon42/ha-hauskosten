---
description: Run release preparation checklist (release-manager)
---

# Release vorbereiten

Delegiere die Aufgabe an den `release-manager` Sub-Agent. Der führt:

1. **Version bestimmen** — Patch / Minor / Major aus Conventional Commits seit letztem Tag ableiten
2. **Vorbedingungen prüfen:**
   - Main-Branch sauber (`git status`)
   - Alle Tests grün auf main (`gh run list --workflow=test.yml --limit 1`)
   - Alle Lint-Checks grün
   - Codacy-Grade A oder B
3. **CHANGELOG finalisieren** — mit `docs-writer` zusammen: `[Unreleased]` → `[X.Y.Z] - YYYY-MM-DD`
4. **Version-Bump** in `custom_components/hauskosten/manifest.json`
5. **Commit** `chore(release): vX.Y.Z`
6. **Tag** `git tag -a vX.Y.Z -m "Release vX.Y.Z"`
7. **Push** `git push && git push --tags`
8. **Release** automatisch via `.github/workflows/release.yml`
9. **Verifizieren** dass GitHub Release erstellt wurde mit `gh release view vX.Y.Z`

Bei ersten stabilen Release (v1.0.0): Zusätzlich HACS-Default-Repo-PR vorbereiten.
