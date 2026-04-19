# Architektur

> Dieser Text ist für **Agents** und **Contributor** geschrieben. Wenn du nur die Integration nutzen willst, siehe `README.md`.

## Mission

`ha-hauskosten` ist eine Home-Assistant-Custom-Integration, die **keine externen APIs** aufruft und **keinen State in die Welt schreibt**. Sie ist reine Buchhaltungs-Logik für Mehrparteien-Wohngebäude.

Drei Kernfragen, die die Integration beantwortet:

1. Welche Kosten fallen in welcher Partei pro Monat / Jahr an?
2. Wann ist die nächste Zahlung fällig?
3. Wie verteilen sich verbrauchsbasierte Kosten (Wasser, Heizstrom, ...) auf die Parteien?

## Abgrenzung (was diese Integration **nicht** ist)

- Kein **Buchhaltungsprogramm**. Wer echte Buchhaltung will → Firefly III.
- Keine **Abrechnungserzeugung** nach BetrKV / HeizkostenV. Wir tracken, wir rechnen nicht rechtsverbindlich ab.
- Kein **Mietzahlungs-Tracker**. Mieten sind Einnahmen, nicht Kosten.
- Kein **Multi-Property-Tool**. Ein Haus pro Integration-Instanz. Mehrere Häuser = mehrere Config-Entries.

## High-Level-Flow

```
┌──────────────────────────────────────────────────────────────────┐
│                     User (via HA-Frontend)                        │
└────────────┬──────────────────────────────┬──────────────────────┘
             │                              │
             ▼                              ▼
    Config Flow (Setup)            Options Flow / Subentries
             │                              │
             └────────────┬─────────────────┘
                          │
                          ▼
                  ┌───────────────┐
                  │  Storage API  │   (JSON in .storage/hauskosten.{entry_id})
                  └───────┬───────┘
                          │
                          ▼
          ┌────────────────────────────────┐
          │    DataUpdateCoordinator       │
          │  (Intervall: 30 min, Trigger:  │
          │   State-Change auf Verbrauchs-│
          │   Entities)                    │
          └──┬─────────────┬─────────────┬─┘
             │             │             │
             ▼             ▼             ▼
     distribution.py  calculations.py  Entity-Reads
     (pure logic)     (pure logic)     (async)
             │             │             │
             └─────┬───────┴─────────────┘
                   ▼
         Aggregate Result Dict
                   │
                   ▼
       ┌───────────────────────┐
       │ Sensor Platform       │
       │  - pro Partei         │
       │  - pro Kategorie      │
       │  - Haus-Gesamt        │
       │  - Fälligkeiten       │
       └───────────────────────┘
```

## Komponenten im Detail

### 1. Config Flow mit Subentries

`config_flow.py` steuert den gesamten User-Input. Die Integration nutzt **Config Subentries**, ein HA-Feature, das erlaubt, unter einem Config-Entry hierarchisch untergeordnete Konfigurationen zu pflegen. Jeder Subentry-Typ hat seinen eigenen Flow.

**Subentry-Typen:**

1. `partei` — Wohneinheit: Name, m², Personen, Bewohnungs-Start/-Ende
2. `kostenposition` — Einzelne Kostenposition (siehe `docs/DATA_MODEL.md`)

**Wichtig:** Bei größeren Änderungen (z. B. Partei löschen, auf die aktive Kostenpositionen verweisen) muss der Coordinator einen „dirty state" erkennen und neu berechnen.

### 2. Storage Layer

Persistierte Daten werden mit `homeassistant.helpers.storage.Store` verwaltet. Pro Config-Entry eine Store-Datei. Der Store enthält ausschließlich Daten, die Subentries **nicht** abbilden können — z. B. ad-hoc via Service-Call hinzugefügte einmalige Kosten oder „mark_paid"-Zeitstempel.

**Warum zwei Stellen (Subentries + Store)?** Subentries sind optimal für vom User gepflegte Stammdaten, haben aber eine rigide Struktur (ein `voluptuous`-Schema pro Subentry-Typ). Der Store ergänzt flexible Runtime-Daten (ad-hoc-Kosten, Zahlungs-Historie).

### 3. Coordinator (`coordinator.py`)

Der `HauskostenCoordinator` erbt von `DataUpdateCoordinator`.

**Update-Zyklus:**

1. Lese alle Verbrauchs-Entities, deren `entity_id` in einer Kostenposition referenziert ist
2. Rufe `calculations.resolve_periodic_amounts()` für alle pauschalen Kosten auf
3. Rufe `distribution.allocate()` für alle Kostenpositionen auf, mit Parteien als Empfängern
4. Aggregiere das Ergebnis zu einer hierarchischen Daten-Struktur (siehe `CoordinatorData` in `models.py`)
5. Markiere die nächste Fälligkeit

**Polling-Intervall:** 30 Minuten ist der Default. Bei State-Changes auf referenzierten Verbrauchs-Entities wird ein Re-Compute getriggert (via `async_track_state_change_event`).

**Wichtig:** Der Coordinator ist **pure aggregation** — keine Persistierung, keine Side-Effects. Das macht ihn testbar.

### 4. Pure Logic Modules

Zwei Module enthalten die gesamte Geschäftslogik. Sie haben **keine HA-Abhängigkeiten** und sind zu 100 % unit-testbar.

- `distribution.py` — Verteilungs-Algorithmen: `equal()`, `by_area()`, `by_persons()`, `by_consumption()`, `direct()`
- `calculations.py` — Zeit-Logik: `annualize()`, `monthly_share()`, `next_due_date()`, `active_in_period()`

Siehe `docs/DISTRIBUTION.md` für formale Spezifikation.

### 5. Sensor-Platform (`sensor.py`)

Sensoren werden **dynamisch** aus dem Coordinator-State generiert. Für jede Partei entstehen:

- `sensor.hauskosten_{partei_slug}_monat_aktuell` — Kosten laufender Monat (€)
- `sensor.hauskosten_{partei_slug}_jahr_aktuell` — Kosten laufendes Jahr (€)
- `sensor.hauskosten_{partei_slug}_jahr_budget` — Jahres-Soll (€)
- `sensor.hauskosten_{partei_slug}_naechste_faelligkeit` — Datum der nächsten Zahlung

Pro Kategorie × Partei zusätzlich:
- `sensor.hauskosten_{partei_slug}_{kategorie}_jahr` — z. B. `hauskosten_og_versicherung_jahr`

Haus-Aggregate:
- `sensor.hauskosten_haus_jahr_gesamt`
- `sensor.hauskosten_haus_{kategorie}_jahr`

**Attribute:** Jeder Sensor exponiert unter `extra_state_attributes` seine Quell-Positionen, so dass Frontend-Cards das aufschlüsseln können.

### 6. Service Actions (`services.yaml`)

- `hauskosten.add_einmalig` — Ad-hoc-Kostenposition hinzufügen (Reparatur, Handwerker)
- `hauskosten.mark_paid` — Eine Kostenposition als bezahlt markieren
- `hauskosten.reset_year` — Am Jahresende Zähler zurücksetzen (optional manuell)

## Entity-Naming-Konvention

- **Device:** Pro Config-Entry ein Device namens „Hauskosten {haus_name}"
- **Entity IDs:** `sensor.hauskosten_<partei_slug>_<zweck>[_<kategorie>]`
- **Slugs:** `partei.name` wird via `slugify()` normalisiert, keine Umlaute

## Fehlerbehandlung

- **Fehlende Verbrauchs-Entity:** Sensor wird `unavailable`, `_LOGGER.warning()` mit `entity_id` und `kostenposition_id`. Kein Crash.
- **Ungültige Konfiguration:** `ConfigEntryError` bei Setup, führt zu Repair-Flow.
- **Dateisystem-Fehler bei Store:** `ConfigEntryNotReady`, HA retryet.
- **Division-durch-Null bei Verteilung:** Position wird im Ergebnis als `error` markiert, nicht 0 € — damit der User sieht, dass etwas fehlt.

## Performance-Budget

- **Coordinator-Update:** < 100 ms bei typischem Setup (5 Parteien, 30 Kostenpositionen)
- **Startup:** < 500 ms
- **Memory:** < 5 MB resident pro Config-Entry

## Testing-Strategie

- **Pure Logic (`distribution.py`, `calculations.py`):** 100 % Line-Coverage, parametrisierte Tests für alle Verteilschlüssel × Edge Cases
- **Config Flow:** Happy Path + jeder Validation-Error-Pfad
- **Coordinator:** Mit Mock-Entities, verifiziert Aggregation korrekt
- **Sensor-Platform:** Smoke-Test, dass Entities erzeugt werden und Coordinator-Updates propagieren

Framework: `pytest-homeassistant-custom-component`.

## Wachstumsplan

| Version | Scope |
|---|---|
| 0.1.0 | MVP: Pauschal-Kosten, `gleich` / `qm` / `personen`-Verteilung, Sensoren |
| 0.2.0 | Verbrauchs-Kosten mit Entity-Referenz |
| 0.3.0 | Service-Actions (add_einmalig, mark_paid), Fälligkeits-Reminder |
| 0.4.0 | Frontend-Card (optional eigenes Repo) |
| 1.0.0 | HACS-Default-Submission, Quality-Scale Silver |

## Offene Architektur-Fragen

Diese Punkte sind **noch nicht entschieden** und brauchen Architect-Input, bevor ein Agent dort Code schreibt.

1. **Utility-Meter-Integration**: Soll die Integration eigene Utility-Meter anlegen oder referenziert sie extern gepflegte? (Tendenz: extern, weniger Magie)
2. **Jahres-Rollover**: Manuell via Service oder automatisch am 01.01.? (Tendenz: manuell, weil User manchmal am 31.12. noch Posten einträgt)
3. **Leerstand**: Eigene implizite „Eigentümer"-Partei oder wird Leerstand auf verbleibende Parteien umgelegt? (Tendenz: eigene Partei, für Klarheit)
4. **Index-Anpassung**: Kostenpositionen, deren Betrag jährlich steigt (Versicherungsindex) — via `options.index_pct` oder manuelle Updates? (Tendenz: MVP manuell, später Feature)
