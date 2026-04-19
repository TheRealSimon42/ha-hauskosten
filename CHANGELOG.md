# Changelog

Alle relevanten Änderungen werden hier dokumentiert.

Format basiert auf [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
Versionierung folgt [Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

### Added

- Projekt-Setup: `AGENTS.md`, Architektur-Dokumente, Sub-Agent-Definitionen
- Coding-Standards und Pre-Commit-Hooks
- Manifest-, HACS- und EditorConfig-Grundgerüst
- Pure-Logik-Modul `distribution.py` mit allen Verteilungsalgorithmen
  (`direkt`, `gleich`, `flaeche`, `personen`, `verbrauch` Subzähler) inkl.
  Zeitgewichtung bei Mieterwechsel und Rundungskorrektur. Vollständige
  Testabdeckung (100 % Line + Branch) via `tests/test_distribution.py`.
- Pure-Logik-Modul `calculations.py` mit Zeit- und Betragsrechnung:
  `annualize`, `monthly_share`, `next_due_date`, `active_in_period`,
  `days_overlap`, `effektive_tage`, `resolve_verbrauchs_betrag`.
  Vollständige Testabdeckung (100 % Line + Branch) via
  `tests/test_calculations.py`.
- Datenmodell-Modul `models.py` finalisiert gegen `docs/DATA_MODEL.md`:
  Google-Style-Docstrings für alle TypedDicts und StrEnums, expliziter
  `__all__`-Export. Smoke-Tests in `tests/test_models.py` verankern die
  Enum-Werte (Schema-Kontrakt) und die TypedDict-Feldmengen.

### Changed

- `tests/conftest.py`: Plugin `pytest_homeassistant_custom_component` wird
  nur geladen wenn installiert, damit Pure-Logik-Tests auch in schlanken
  Umgebungen laufen.
- `pyproject.toml`: Ruff-Regel `TRY003` ignoriert (lange Fehlermeldungen
  inline sind bewusst erlaubt für bessere Diagnose).
- `pyproject.toml`: Coverage-Gate `fail_under` von `0` auf `80` angehoben,
  nachdem distribution / calculations / models vollständig getestet sind
  (Gesamt-Coverage aktuell 95 %).

### Fixed

- —

[Unreleased]: https://github.com/TheRealSimon42/ha-hauskosten/compare/HEAD...HEAD
