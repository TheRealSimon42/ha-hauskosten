# Coding Standards

Dieses Dokument hält die verbindlichen Standards fest. Alles andere ist Konvention aus dem HA-Core / HACS-Ökosystem.

## Python

### Version & Features

- **Python 3.13+** (aktuell mit HA Core 2026.x)
- `from __future__ import annotations` in jeder Datei
- `TypedDict`, `StrEnum`, `Protocol` bevorzugt gegenüber `Union`-Typen
- Keine f-String-Logs mit Args — `_LOGGER.info("foo %s", bar)` (lazy evaluation)

### Formatierung

- **Formatter:** `ruff format` (Line-Length 88, Black-kompatibel)
- **Import-Order:** via `ruff check --select I` (isort-Style), Gruppen: stdlib → third-party → `homeassistant` → local
- Keine relativen Imports außer `.const`, `.models` innerhalb des Pakets
- Trailing Comma in Multi-Line-Collections Pflicht

### Linting

Aktivierte Ruff-Regelsets (`pyproject.toml`):

- `E`, `W` — pycodestyle
- `F` — Pyflakes
- `I` — isort
- `B` — bugbear
- `UP` — pyupgrade (Python 3.13 Targets)
- `SIM` — simplify
- `RUF` — Ruff-spezifisch
- `C4` — comprehensions
- `N` — naming
- `D` — pydocstyle (Google-Convention)
- `ANN` — type annotations
- `S` — bandit (Security)
- `TID` — tidy imports
- `ARG` — unused arguments
- `PTH` — use pathlib
- `PL` — pylint subset

Ignoriert (mit Begründung):
- `D203`, `D213` — stilistische Konflikte mit Google-Convention
- `ANN401` — `Any` ist manchmal legitim (z. B. HA-Config-Dicts)

### Typing

- **`mypy --strict`** auf `custom_components/hauskosten/`
- Keine `type: ignore`-Kommentare ohne Grund — wenn nötig, mit `# type: ignore[specific-error]  # reason`
- Alle öffentlichen Funktionen haben Return-Typ-Annotation
- Generische Typen mit dem neuen Python-3.12+-Syntax: `def foo[T](x: T) -> T`

### Docstrings

Google-Style, mit mindestens:

```python
def allocate(betrag: float, parteien: list[Partei]) -> dict[str, float]:
    """Verteile einen Betrag auf Parteien.

    Args:
        betrag: Gesamtbetrag in Euro.
        parteien: Aktive Parteien zum Stichtag.

    Returns:
        Dictionary {partei_id: anteil_eur}.

    Raises:
        ValueError: Wenn `parteien` leer ist.
    """
```

## YAML / JSON

- **Einrückung:** 2 Spaces, niemals Tabs
- **Keys:** `snake_case` in allen Files
- `strings.json`: der User liest das nie, aber Agents kopieren daraus — darum saubere Strukturierung nach Schlüssel-Hierarchie der HA-Translation-Docs
- `manifest.json`: Version folgt SemVer, nicht CalVer

## Commits

**Conventional Commits:**

```
feat: add personen distribution key
fix(coordinator): handle missing entity gracefully
refactor(config_flow): extract subentry validation
docs: clarify leerstand edge case
test(distribution): add rounding test cases
chore(deps): bump homeassistant to 2026.4.0
```

Typen: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`, `style`.

Breaking Changes im Footer:

```
feat: rename "anteil" to "share" in CoordinatorData

BREAKING CHANGE: Custom Frontend-Cards must update their attribute lookups.
```

## Pull Requests

- **Titel:** Wie Commit-Message.
- **Description:** Was, Warum, Wie getestet, Changelog-Eintrag.
- **Size:** < 400 geänderte Zeilen bevorzugt, größere PRs brauchen Begründung.
- **Tests:** Jede neue Logik braucht einen Test. Bug-Fixes brauchen einen Regression-Test.
- **CI:** `lint`, `test`, `validate` (hassfest + HACS) müssen grün sein.

## Codacy

- **Grade-Gate:** A oder B — C ist nicht akzeptabel
- **Coverage-Gate:** ≥ 80 % Line-Coverage auf `custom_components/hauskosten/`
- **Complexity:** Max. zyklomatische Komplexität 10 pro Funktion
- **Duplication:** Keine Duplikat-Blöcke > 50 Zeilen

Konfiguration in `.codacy.yaml` im Root.

## Home-Assistant-Spezifika

### Config Flow

- Verwende `SectionConfig` für logische Gruppen (HA 2024.6+)
- Kein `errors=...` ohne Translation-Key
- Alle User-Facing-Texte in `translations/de.json` und `translations/en.json`
- Reconfiguration-Flow implementiert für alle Subentries (HA 2024.12+)

### Entity-Design

- Entities haben `_attr_has_entity_name = True` und `name = None` für Device-Name
- `unique_id` ist stabil: `{entry_id}_{partei_id}_{zweck}`
- `device_class` wo möglich: `MONETARY` für €-Werte, `DATE` für Fälligkeiten
- `state_class` bei €-Werten: `TOTAL` (nicht `MEASUREMENT`, weil Monatsreset)

### Coordinator

- Erbt von `DataUpdateCoordinator[CoordinatorData]` (generic)
- `update_interval = timedelta(minutes=30)` als Default
- `_async_update_data()` raised `UpdateFailed` bei Problemen, nicht beliebige Exceptions
- Listener auf State-Changes via `async_track_state_change_event`, registriert in `async_setup_entry`, abgemeldet in `async_unload_entry`

### Services

- `services.yaml` vollständig, mit Deutsch-Übersetzungen
- Jeder Service hat Selector-Definitionen (z. B. `selector: {entity: {domain: sensor}}`)
- Service-Funktionen nehmen `ServiceCall`, validieren via `vol.Schema` wenn nötig

## Tests

### Framework

- `pytest` + `pytest-homeassistant-custom-component` + `pytest-asyncio`
- `conftest.py` mit Standard-Fixtures
- Snapshot-Tests via `syrupy` für CoordinatorData-Output

### Struktur

```
tests/
├── conftest.py              # Shared fixtures
├── test_distribution.py     # Pure logic tests
├── test_calculations.py     # Pure logic tests
├── test_config_flow.py      # UI flow tests
├── test_coordinator.py      # Integration-style
├── test_sensor.py           # Entity tests
├── test_services.py         # Service call tests
├── test_init.py             # Setup / unload / migration
└── fixtures/
    ├── single_party.json
    ├── mfh_typical.json     # 2 Parteien, 10 Kostenpositionen
    └── edge_cases.json
```

### Konventionen

- Test-Namen: `test_<unit>_<scenario>_<expected>`, z. B. `test_allocate_flaeche_zeit_gewichtet_rundet_korrekt`
- Parametrize statt Loops
- Fixtures aus `fixtures/*.json` geladen, nicht inline geschrieben
- Keine `time.sleep` / `asyncio.sleep` — `hass.async_run_until_complete` nutzen

## Editor-Config

`.editorconfig` im Root:

```
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true

[*.py]
indent_style = space
indent_size = 4

[*.{yaml,yml,json}]
indent_style = space
indent_size = 2

[*.md]
trim_trailing_whitespace = false  # Trailing two spaces = line break
```

## Pre-Commit

Pflicht. Installation bei `git clone`:

```bash
pip install pre-commit
pre-commit install
```

Hooks: `ruff-format`, `ruff-check`, `mypy`, `check-yaml`, `check-json`, `check-merge-conflict`, `end-of-file-fixer`, `trailing-whitespace`.

## Unverhandelbar

Diese Punkte sind die Standards-äquivalente der `AGENTS.md`-Hard-Constraints:

1. **Kein Code ohne Tests.** Ein PR, der eine neue Funktion ohne Test einführt, wird nicht gemergt.
2. **Keine Logik in Entity-Properties.** Properties lesen nur aus `coordinator.data`, rechnen nichts selbst.
3. **Keine Zeitzonen-Fehler.** `homeassistant.util.dt` für alles Datum/Zeit, niemals `datetime.now()` ohne TZ.
4. **Kein Magic String.** User-sichtbare Texte via Translations, interne Strings als Enum-Werte.
