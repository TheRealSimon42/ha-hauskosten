# Standards Adaptation

Dieses Dokument hält fest, welche Standards aus dem Referenzprojekt `simon42-dashboard-strategy` (TypeScript-Frontend) für `ha-hauskosten` (Python-Integration) übernommen, angepasst oder bewusst ignoriert werden. Es ist die Grundlage für die Config-Angleichung in Phase 0.x.

**Basis der Analyse:** Vollscan des Referenzprojekts am 2026-04-19. Abgleich mit dem aktuellen Skelett-Stand von `ha-hauskosten`.

---

## 1. Direkt übernehmbar (sprach-agnostisch)

Diese Standards lassen sich ohne Änderung aus dem Referenzprojekt übernehmen und haben konkrete Lücken im aktuellen Skelett.

### 1.1 Stale-Issue-Workflow

**Quelle:** `.github/workflows/stale.yml` (Referenz)
**Status:** Fehlt komplett im Skelett.
**Übernahme:** `actions/stale@v9`, Intervall 06:00 UTC daily, `35d → stale → 7d → close`, deutsche Labels beibehalten (`stale`, `keep-open`).
**Begründung:** Reines GitHub-Actions-Workflow, kein Sprach-Bezug.

### 1.2 Changelog-Automation via `git-chglog`

**Quelle:** `.chglog/config.yml` (Referenz)
**Status:** Fehlt. Aktuell manuelle Pflege in `CHANGELOG.md`.
**Übernahme:** Tag-Filter `^v[0-9]+\.[0-9]+\.[0-9]+$`, Commit-Groups `feat → fix → chore → docs`, Keep-a-Changelog-Format, Sortierung `by author date`.
**Begründung:** `git-chglog` arbeitet auf Git-Tags und Commit-Messages — sprach-unabhängig. Dockt an unsere Conventional-Commits-Pflicht an.

### 1.3 Tag-/Release-Strategie

**Quelle:** Commit-Konvention + Tag-Filter aus `.chglog/config.yml` (Referenz)
**Übernahme:**

- `feat:` → Minor-Bump
- `fix:` → Patch-Bump
- Beta-Releases als `vX.Y.Z-beta.N` (z. B. `v0.1.0-beta.3`)
- Release-Workflow (bereits im Skelett vorhanden) berücksichtigt Pre-Release-Flag via `contains(VERSION, '-')` ✓

**Begründung:** SemVer + Conventional Commits sind bereits in `docs/STANDARDS.md` dokumentiert. Nur Beta-Suffix-Pattern ergänzen.

### 1.4 Issue-Template-Struktur (Blank-Issues blockieren)

**Quelle:** `.github/ISSUE_TEMPLATE/config.yml` (Referenz)
**Status:** Skelett hat `bug_report.yml` und `feature_request.yml`, aber kein `config.yml`.
**Übernahme:** `config.yml` mit `blank_issues_enabled: false` und Contact-Links (Community, YouTube).
**Begründung:** Schiebt User zu den strukturierten Templates und hält Blank-Issues fern.

### 1.5 License-Setup

**Quelle:** `LICENSE` (CC BY-NC-SA 4.0, Langtext)
**Status:** Identisch in beiden Projekten ✓. SPDX-Kennung `CC-BY-NC-SA-4.0` ist in `pyproject.toml` schon korrekt gesetzt.
**Aktion:** Keine. Bereits konsistent.

### 1.6 README-Sektions-Pattern

**Quelle:** `README.md` (Referenz)
**Status:** Skelett-README folgt bereits dem gleichen Muster (Badges oben, YouTube-Membership, Buy Me A Coffee, Features, Installation, Roadmap).
**Aktion:** Keine Struktur-Änderung nötig. Ggf. Badge-Set harmonisieren (HACS-Badge, GitHub-Release, Stars, License — alle bereits geplant).

---

## 2. Anzupassen (JS → Python-Pendant)

Diese Standards existieren im Referenzprojekt für JavaScript/TypeScript und brauchen ein Python-Äquivalent.

### 2.1 Codacy-Engines

**Referenz:** `eslint9`, `opengrep`, `lizard`, `trivy` aktiv
**Python-Pendant (aktuelles Skelett):** `ruff`, `mypy`, `radon` (Complexity ≡ `lizard`), `bandit`

**Lücke:** `trivy` (Dependency-Vuln-Scan) hat kein direktes Äquivalent in der aktuellen Codacy-Config.
**Entscheidung:** Trivy wird aktiviert. **Primär via Codacy** (`.codacy.yaml`), falls Codacy für Python-Repos unterstützt; **Fallback** als separater GitHub-Workflow `security-scan.yml` mit `aquasecurity/trivy-action`. Scannt `manifest.json`-`requirements` und `pyproject.toml`.

### 2.2 Editor-Konfiguration (Line-Length, Indent)

**Referenz:** `max_line_length=120`, `indent_size=2` global (TS/JS-Convention)
**Python-Entscheidung (dokumentiert):** Line-Length **88** (`docs/STANDARDS.md`, Black-kompatibel), Indent **4** für Python, **2** für YAML/JSON.
**Aktion:** **Nichts übernehmen** — die Python-Werte sind Pflicht (PEP 8 + Ruff-Default). Die Divergenz ist bewusst und korrekt.
**Dokumentation:** In `docs/STANDARDS.md` ggf. klarstellen, dass die 88/4-Konvention gegen Ref-Projekt-Werte gesetzt ist, falls jemand darauf stößt.

### 2.3 Formatter/Linter (Prettier/ESLint → ruff)

**Referenz:** Prettier 3.8.1 + ESLint 10.2.0 + `eslint.config.mjs` (FlatConfig)
**Python-Pendant:** `ruff format` + `ruff check` (bereits komplett in `pyproject.toml` + `.pre-commit-config.yaml` abgebildet) ✓
**Aktion:** Keine. Äquivalenz vollständig. Ref-Features wie Single-Quote-Style wurden bereits auf `quote-style = "double"` (Python-Konvention) adaptiert.

### 2.4 PR-Template („Test plan")

**Referenz:** Structured Checklist mit `- [ ] npm run build erfolgreich`, Dashboard-spezifische Smoke-Tests.
**Skelett-Stand:** PR-Template ist bereits umfangreich (Checklist mit ruff/mypy/pytest/CHANGELOG/Translations).
**Aktion:** **Kleine Ergänzung**: „Integration in lokaler HA-Dev-Instanz manuell getestet" als optionalen Punkt ergänzen (Analog zum „Dashboard lädt fehlerfrei" im Ref-Projekt).

### 2.5 Package-Scripts → Makefile

**Referenz:** `package.json` mit `build`, `watch`, `format`, `lint:fix` — npm-Scripts als zentrale Dev-Entrypoints.
**Python-Pendant:** Aktuell keine zentralen Scripts. Entwickler tippen `ruff check`, `pytest` etc. manuell.
**Entscheidung:** **Makefile** in Phase 0.x einführen. Targets: `fmt`, `lint`, `type`, `test`, `cov`, `all`, `clean`.
**Begründung (Makefile statt Nox):**

- Kein zusätzlicher Python-Dependency für die Dev-Setup-Schwelle
- Universell verständlich, auch ohne Python-Kontext
- Wir targeten **nur Python 3.13** — Nox glänzt bei Multi-Version-Matrix-Tests, die wir nicht brauchen
- Mini-Overhead: ~15 Zeilen Makefile ersetzen `noxfile.py` + Nox-Install komplett

---

## 3. Nicht relevant (JS-spezifisch)

Diese Artefakte aus dem Referenzprojekt haben keine sinnvolle Python-Entsprechung und werden bewusst **nicht** übernommen.

| Artefakt (Referenz) | Warum nicht | Python-Alternative (falls relevant) |
|---|---|---|
| `.prettierrc` | Formatierung via `ruff format` | — |
| `.biome.json` | Biome ist JS-Tooling | — |
| `eslint.config.mjs` + `@typescript-eslint` | JS/TS-Linter | `ruff check` |
| `package.json`, `package-lock.json` | npm-Ökosystem | `pyproject.toml` |
| `tsconfig.json`, `webpack.config.*` | Build-Pipeline für Browser | Keine — Integration ist Pure Python |
| `dist/`-Output | JS-Bundling | Keine — HA importiert `custom_components/` direkt |
| `src/types/**`-Exclude in Codacy | TS-generated Types | — |
| Dependabot (nicht vorhanden im Ref) | — | Optional: später `.github/dependabot.yml` für `pip` + `github-actions`-Ecosystem |

---

## 4. Priorisierte Änderungs-Liste für Schritt 0.3

Reihenfolge für die Config-Angleichung (ein Commit pro File):

1. **`.github/workflows/stale.yml` hinzufügen** — Pattern 1:1 aus Ref übernehmen, deutsche Labels.
2. **`.github/ISSUE_TEMPLATE/config.yml` hinzufügen** — `blank_issues_enabled: false` + Contact-Links.
3. **`.chglog/config.yml` + `.chglog/CHANGELOG.tpl.md` hinzufügen** — Conventional-Commits → Keep-a-Changelog; Release-Workflow so erweitern, dass `git-chglog` beim Tag-Push den Changelog automatisch regeneriert.
4. **`.codacy.yaml` — Trivy Engine aktivieren** (primär); falls Codacy-API Trivy für Python-Repos nicht abdeckt, `.github/workflows/security-scan.yml` als Fallback mit `aquasecurity/trivy-action` anlegen.
5. **PR-Template ergänzen** — optionale Zeile für manuellen HA-Dev-Test.
6. **`pyproject.toml` Review** — Line-Length 88 und Ruff-Select-Set sind konsistent mit `docs/STANDARDS.md`; nur Abgleich, keine Änderung erwartet.
7. **Workflow-Review** — bereits vorhandene `lint.yml` / `test.yml` / `validate.yml` / `release.yml` gegen Ref-Validate-Workflow gegenchecken (Action-Versionen, Trigger, Permissions).
8. **`Makefile` hinzufügen** — Dev-Entry-Points: `fmt`, `lint`, `type`, `test`, `cov`, `all`, `clean`. README-Setup-Sektion darauf verweisen.

**Nicht in Phase 0.x:** Dependabot (nachgelagerter Task, sobald Release 0.1.0 raus ist).

---

## 5. Entschiedene Fragen

| Frage | Entscheidung |
|---|---|
| Trivy via Codacy oder eigener Workflow? | Primär Codacy, Fallback eigener `security-scan.yml`-Workflow |
| Changelog manuell oder auto via `git-chglog`? | Auto-Regeneration beim Release-Tag, `.chglog/`-Setup |
| Makefile oder Nox? | **Makefile** — kein extra Dependency, universell |

Freigabe der Datei durch User → Start mit Schritt 0.3.
