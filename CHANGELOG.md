# Changelog

Alle relevanten Änderungen werden hier dokumentiert.

Format basiert auf [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
Versionierung folgt [Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

### Added

- **Abschlag-Modus** (`Betragsmodus.ABSCHLAG`) für Kostenpositionen nach dem
  Muster "monatlicher Abschlag + Jahresabrechnung" (Wasser, Heizstrom,
  Abwasser, …). Neue Felder an `Kostenposition`:
  `monatlicher_abschlag_eur`, `abrechnungszeitraum_start`,
  `abrechnungszeitraum_dauer_monate` (default 12). Validierungs-Matrix
  deckt `HAUS × ABSCHLAG` (gleich/flaeche/personen/subzaehler) und
  `PARTEI × ABSCHLAG` (direkt) ab. Closes #10.
- Pure-Logik-Helfer in `calculations.py`: `vergangene_monate`,
  `abschlaege_gezahlt`, `abschlag_ist_kosten`, `abschlag_saldo`,
  `abschlag_zeitraum_ende` — 100 % Coverage.
- Coordinator ruft die HA `recorder.statistics.statistic_during_period`
  API auf, um den Verbrauch über den Abrechnungszeitraum zu ermitteln
  (Zähler-Entity mit `state_class=total_increasing` empfohlen).
  Recorder-Import ist lazy: nur wenn mindestens eine ABSCHLAG-Position
  konfiguriert ist. Fällt Statistics aus, bleibt `abschlag_ist_eur_jahr`
  / `abschlag_saldo_eur_jahr` auf `None` (Sensor "unavailable").
- Neue `PositionAttribution`-Felder: `abschlag_gezahlt_eur_jahr`,
  `abschlag_ist_eur_jahr`, `abschlag_saldo_eur_jahr` (alle
  `float | None`). Für nicht-ABSCHLAG-Positionen stets `None`.
- Sechs neue Sensor-Klassen — drei pro Partei × Position
  (`abschlag_gezahlt`, `abschlag_ist`, `abschlag_saldo`) und drei pro
  Haus × Position als Summen über alle Parteien. Dynamische Erzeugung
  aus den Subentries.
- Config Flow: ABSCHLAG-Zweig im Details-Step mit dem monatlichen
  Abschlag, Zeitraum-Anker, Dauer in Monaten und optionalen
  Verbrauchs-Feldern (Entity / Einheitspreis / Einheit / Grundgebühr).
  Entity+Preis+Einheit sind gekoppelt: gesetzte Entity erzwingt die
  beiden anderen.
- Service `hauskosten.jahresabrechnung_buchen`: bucht die
  Jahresabrechnung einer ABSCHLAG-Position. Der Delta zu den geleisteten
  Abschlägen wird bei Nachzahlung als `AdHocKosten` mit gleicher
  Kategorie / Zuordnung / Verteilung eingebucht, der
  Abrechnungszeitraum-Anker rollt danach automatisch um
  `abrechnungszeitraum_dauer_monate` weiter. Guthaben (Final < Gezahlt)
  erzeugt kein AdHoc, rollt aber ebenfalls.
- Schema-Version v2 mit Migration v1 → v2, die bestehende
  Kostenposition-Subentries die drei neuen Felder als `None` ergänzt und
  `entry.version` anhebt. Config-Flow-`VERSION` ebenfalls auf 2.
- Neue Translations (DE + EN) für alle ABSCHLAG-Texte, Sensor-Namen und
  Service-Felder.

### Changed

- `_validate_details_input` in `config_flow.py` wurde in drei
  Modus-spezifische Helper extrahiert (`_validate_pauschal`,
  `_validate_verbrauch`, `_validate_abschlag`), um die zyklomatische
  Komplexität unter der Codacy-Schwelle zu halten.
- `_build_sensors` in `sensor.py` in `_build_partei_sensors` und
  `_build_haus_sensors` zerlegt (gleicher Grund).

### Fixed

- —

## [0.1.0-beta.1] - 2026-04-19

Erster öffentlicher Pre-Release. Phase 1 (1.1–1.8) ist komplett: pure
Verteilungs- und Zeitlogik, Datenmodell, Storage, Coordinator, vollständiger
Config Flow mit Subentries, Integrations-Lifecycle inkl. Services und die
dynamische Sensor-Platform. 310 Tests, Gesamt-Coverage 97.84 %.

### Added

- Projekt-Setup: `AGENTS.md`, Architektur-Dokumente, Sub-Agent-Definitionen.
- Coding-Standards und Pre-Commit-Hooks.
- Manifest-, HACS- und EditorConfig-Grundgerüst.
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
    `tests/test_config_flow.py` (45 Tests).
- Integration-Lifecycle in `__init__.py` final: `async_setup_entry`
  orchestriert Store-Load (Fehler → `ConfigEntryNotReady`), Coordinator-
  Konstruktion + `async_config_entry_first_refresh`, State-Listener,
  `hass.data[DOMAIN][entry_id]` mit `store` + `coordinator`,
  `add_update_listener` (via `async_on_unload`) für Subentry-Refresh
  und Platform-Forward an `sensor`. `async_unload_entry` demontiert
  Listener, unloadet Platforms, räumt `hass.data` auf und entfernt
  Services beim letzten Entry. `async_entry_update_listener` rewired
  State-Listener und ruft `async_request_refresh`. `async_migrate_entry`
  akzeptiert die aktuelle Schema-Version v1 und weist höhere Versionen
  (Downgrade) ab.
- Service-Actions `hauskosten.add_einmalig` und `hauskosten.mark_paid`
  in `services.py` + `services.yaml`; optional `entry_id` (Auto-Pick
  bei genau einem Entry, sonst Pflicht). `add_einmalig` generiert
  UUIDs, schreibt via `HauskostenStore.async_add_adhoc` und löst
  `coordinator.async_request_refresh` aus; `mark_paid` schreibt via
  `async_mark_paid`. `ServiceValidationError` bei unbekanntem
  `entry_id`, fehlender Disambiguierung oder Duplikat-IDs.
- Service-Felder in `strings.json` + `translations/de.json` +
  `translations/en.json` (Name + Description pro Feld für
  `add_einmalig` und `mark_paid`).
- `tests/test_init.py` (20 Tests): Setup-Happy-Path inkl. Store- und
  Coordinator-Wiring, State-Listener-Refresh bei Verbrauchs-Entity-
  Änderung, Store-Load-Failure → `ConfigEntryNotReady`, Unload mit
  Listener- und hass.data-Cleanup, Update-Listener triggert
  Coordinator-Refresh, Migration-Contract (aktuelle Version / Downgrade),
  Services-Registrierung + Auslösung + Entry-Resolution inkl.
  Multi-Entry-Disambiguierung.
- Sensor-Platform `sensor.py` final: `async_setup_entry` baut aus
  `coordinator.data` dynamisch alle Entities. Pro Partei: `monat_aktuell`,
  `jahr_aktuell`, `jahr_budget`, `naechste_faelligkeit` (DATE) plus ein
  `kategorie_<kategorie>_jahr`-Sensor pro Kategorie mit Beitrag > 0. Fürs
  ganze Haus: `jahr_gesamt`, `jahr_budget`, `naechste_faelligkeit` (früheste
  Fälligkeit aller Parteien) und ein `kategorie_<kategorie>_jahr`-Sensor
  pro Haus-weiter Kategorie. Alle Entity-Klassen erben
  `HauskostenSensorBase` (`CoordinatorEntity` + `SensorEntity`) mit
  gemeinsamer `DeviceInfo` (ein Device pro ConfigEntry) und gesetztem
  `_attr_has_entity_name = True`. Unique-IDs rein auf IDs:
  `{entry_id}_partei_{subentry_id}_{zweck}` bzw.
  `{entry_id}_haus_{zweck}` — keine Abhängigkeit vom Partei-Namen.
  EUR-Sensoren mit `device_class=MONETARY`, `state_class=TOTAL`,
  `native_unit_of_measurement="EUR"`, `suggested_display_precision=2`;
  Fälligkeits-Sensoren mit `device_class=DATE`. Translation-Keys aus
  `entity.sensor.*` in `strings.json` (`partei_monat_aktuell`,
  `partei_jahr_aktuell`, `partei_jahr_budget`,
  `partei_naechste_faelligkeit`, `partei_kategorie_jahr`, `haus_jahr_gesamt`,
  `haus_jahr_budget`, `haus_kategorie_jahr`, `naechste_faelligkeit`) mit
  `_attr_translation_placeholders` für `partei` / `jahr` / `kategorie`.
  Dynamisches Entity-Management via `coordinator.async_add_listener` —
  neue Parteien / Kategorien erzeugen neue Sensoren ohne Reload; ein
  `known_ids`-Set verhindert Doppelregistrierung. Properties lesen
  ausschließlich aus `coordinator.data`, `available` wird gated auf
  Parteianwesenheit, sodass gelöschte Parteien `unavailable` werden.
- `tests/test_sensor.py` (22 Tests): Empty-Setup (nur Haus-Sensoren),
  Ein-Partei-Ein-Position-Szenario (alle Sensoren + korrekte native_values),
  Zwei Parteien × zwei Kategorien (Kategorie-Sensoren je Partei und Haus),
  Coordinator-Refresh propagiert Werte an Sensoren, dynamische
  Subentry-Ergänzung (neue Partei → neue Sensoren ohne Reload),
  Device-Grouping unter `(DOMAIN, entry_id)`, `has_entity_name=True` +
  `translation_key`, unique_id-Stabilität gegen Partei-Namen,
  Verschwinden der Partei → `unavailable`, Fälligkeits-Sensoren liefern
  ISO-Daten / `unknown`, keine Kategorie-Sensoren für Null-Werte,
  Attribute listen `positionen` / `kategorie`.

### Changed

- `manifest.json`: `integration_type` auf `"hub"` gesetzt, da wir
  Subentries für Parteien und Kostenpositionen verwenden — entspricht
  dem Hassfest-Standard für Multi-Device-/Multi-Record-Integrationen.
- `tests/conftest.py`: Plugin `pytest_homeassistant_custom_component` wird
  nur geladen, wenn installiert, damit Pure-Logik-Tests auch in schlanken
  Umgebungen laufen. Zusätzlich: Nicht existente Einträge aus
  `custom_components.__path__` strippen, damit HAs
  `_get_custom_components` nicht am `__editable__`-Placeholder von
  pip-editable-Installs scheitert.
- `pyproject.toml`: Ruff-Regel `TRY003` ignoriert (lange Fehlermeldungen
  inline sind bewusst erlaubt für bessere Diagnose).
- `pyproject.toml`: Coverage-Gate `fail_under` von `0` auf `80` angehoben,
  nachdem distribution / calculations / models vollständig getestet sind
  (aktuelle Gesamt-Coverage 97.84 %).

### Fixed

- —

[Unreleased]: https://github.com/TheRealSimon42/ha-hauskosten/compare/v0.1.0-beta.1...HEAD
[0.1.0-beta.1]: https://github.com/TheRealSimon42/ha-hauskosten/releases/tag/v0.1.0-beta.1
