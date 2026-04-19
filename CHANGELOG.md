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
- Storage-Schicht `storage.py` (`HauskostenStore`) über
  `homeassistant.helpers.storage.Store`: eine Store-Datei pro
  `ConfigEntry` (Key `hauskosten.{entry_id}`), persistiert
  `ad_hoc_kosten` (Service `add_einmalig`) und `paid_records`
  (Service `mark_paid`). Public API: `async_load`, `async_save`,
  `async_add_adhoc`, `async_remove_adhoc`, `async_mark_paid` plus
  defensive Copy-Getter `adhoc_kosten` / `paid_records`. ISO-Date
  Serialisierung/Deserialisierung mit Warn-Logging bei kaputten
  Feldern, Migrations-Hook (`_async_migrate_func`) für künftige
  Schema-Versionen. 100 % Line + Branch Coverage via
  `tests/test_storage.py`.
- Coordinator `coordinator.py` (`HauskostenCoordinator`) als
  `DataUpdateCoordinator[CoordinatorData]` mit 30-Minuten-Polling
  und State-Change-Listener auf allen referenzierten Verbrauchs-
  Entities. Pipeline: Parteien/Kostenpositionen aus ConfigEntry-
  Subentries normalisieren, Ad-hoc-Kosten aus Store lesen,
  Verbrauchs-Entities lesen (fehlende / unavailable / nicht
  numerisch → `PositionAttribution.error`), `annualize` /
  `resolve_verbrauchs_betrag` auf Betrags-Ebene, `effektive_tage`
  für Zeitgewichtung, `distribution.allocate` pro Position,
  Aggregation in `CoordinatorData` mit Haus-Totals und
  nächstem Fälligkeitsdatum. Distribution-Fehler werden
  positionsweise markiert (Coordinator lebt weiter);
  unerwartete Exceptions werden zu `UpdateFailed`. 100 % Line +
  Branch Coverage auf coordinator.py via `tests/test_coordinator.py`.
- Vollständiger Config-Flow `config_flow.py`:
  * `HauskostenConfigFlow` — Haus anlegen (unique_id-Abort bei
    Duplikaten, server-seitige Name-Validierung).
  * `ParteiSubentryFlow` — Create + Reconfigure für Wohneinheiten
    mit Uniqueness-, Range-, Datumsrange-Validierung und Pre-Fill
    bei Reconfigure.
  * `KostenpositionSubentryFlow` — Multi-Step-Flow (basis →
    details → verteilung → optional subzaehler) mit dynamischen
    Schemas je nach Zuordnung/Betragsmodus. Validierungsmatrix
    aus `docs/DATA_MODEL.md` ist via Table-Lookup
    `_ALLOWED_VERTEILUNGEN` erzwungen; das SelectSelector-Feld
    für `verteilung` zeigt nur gültige Optionen und der Server-Side-
    Validator weist fremde Werte mit `invalid_combination` ab.
    `VERBRAUCH_SUBZAEHLER` öffnet einen zusätzlichen Step mit
    einem `EntitySelector` pro aktiver Partei. Reconfigure lädt
    alle Werte vor und verwendet `async_update_and_abort`.
  * Alle User-Facing-Texte via `strings.json` / `translations/de.json`
    / `translations/en.json` mit vollen Keys für Title, Description,
    Data, Data-Description, Error und Abort pro Step; zentrale
    Selector-Übersetzungen für `kategorie`, `zuordnung`,
    `betragsmodus`, `periodizitaet`, `einheit`, `verteilung`.
  * 94 % Line + Branch Coverage auf config_flow.py via
    `tests/test_config_flow.py` (45 Tests, 268 Tests gesamt,
    Gesamt-Coverage 97.76 %).
- `tests/conftest.py`: Strippt nicht existente Einträge aus
  `custom_components.__path__`, damit HAs `_get_custom_components`
  nicht am `__editable__`-Placeholder von pip-editable-Installs
  scheitert.

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
