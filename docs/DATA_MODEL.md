# Datenmodell

Dieses Dokument ist die **autoritative Quelle** für alle Datenstrukturen in `ha-hauskosten`. Wenn Code und dieses Dokument auseinanderlaufen, ist das Dokument der Plan und der Code der Bug — außer ein Architect hat ausdrücklich eine Änderung hier abgesegnet.

## Konventionen

- Alle Datenstrukturen sind `TypedDict` oder `dataclass(frozen=True)` in `models.py`
- Geldbeträge: `float` mit Einheit €, immer auf 2 Nachkommastellen gerundet bei der Ausgabe
- IDs: `str`, generiert via `uuid.uuid4()` beim Anlegen
- Datumsangaben: `datetime.date` (ohne Uhrzeit)
- Enums: `StrEnum` (Python 3.11+), Werte sind Lowercase-Snake-Case

## Typen-Übersicht

```
HauskostenConfig (root, in ConfigEntry.data)
├── Partei (als Subentry)         [1..n]
└── Kostenposition (als Subentry) [0..n]

AdHocKosten (in Storage, nicht Subentry) [0..n]
```

---

## `Partei`

Eine Wohneinheit im Haus.

```python
class Partei(TypedDict):
    id: str                       # UUID
    name: str                     # z. B. "OG (Simon)", "DG (Mieter)"
    flaeche_qm: float             # Wohnfläche in m²
    personen: int                 # Personenzahl (für personenbasierte Verteilung)
    bewohnt_ab: date              # inklusive
    bewohnt_bis: date | None      # None = unbefristet / aktuell
    hinweis: str | None           # Free-Text (optional)
```

**Validierung:**
- `name`: 1–50 Zeichen, eindeutig pro Config-Entry
- `flaeche_qm`: > 0, < 1000
- `personen`: ≥ 0, ≤ 20 (auch Leerstand = 0 Personen erlaubt)
- `bewohnt_ab` ≤ `bewohnt_bis` (wenn gesetzt)

**Edge Case — Mieterwechsel:** Alte Partei bekommt `bewohnt_bis` gesetzt, neue Partei wird neu angelegt. Die Historie bleibt damit nachvollziehbar.

---

## `Kostenposition`

Eine einzelne Kostenquelle.

```python
class Kostenposition(TypedDict):
    id: str
    bezeichnung: str              # "Gebäudeversicherung", "Müllgebühr 2026"
    kategorie: Kategorie

    # Wem gehört die Kostenposition?
    zuordnung: Zuordnung           # HAUS oder PARTEI
    zuordnung_partei_id: str | None  # nur bei zuordnung=PARTEI

    # Wie wird der Betrag ermittelt?
    betragsmodus: Betragsmodus     # PAUSCHAL oder VERBRAUCH

    # Felder bei PAUSCHAL:
    betrag_eur: float | None
    periodizitaet: Periodizitaet | None
    faelligkeit: date | None       # für Reminder

    # Felder bei VERBRAUCH:
    verbrauchs_entity: str | None  # z. B. "sensor.wasserzaehler"
    einheitspreis_eur: float | None  # pro Einheit (€/m³, €/kWh)
    einheit: Einheit | None
    grundgebuehr_eur_monat: float | None  # optional

    # Wie wird verteilt?
    verteilung: Verteilung
    # Bei VERBRAUCH_SUBZAEHLER:
    verbrauch_entities_pro_partei: dict[str, str] | None
    # ^ {partei_id: entity_id}

    # Bewirtschaftungszeitraum (optional, default = immer aktiv):
    aktiv_ab: date | None
    aktiv_bis: date | None

    # Meta:
    notiz: str | None
```

### Enums

```python
class Kategorie(StrEnum):
    VERSICHERUNG = "versicherung"       # Gebäude, Haftpflicht
    MUELL = "muell"                     # Müllgebühren
    WASSER = "wasser"                   # Frischwasser
    ABWASSER = "abwasser"               # Abwasser / Niederschlagswasser
    STROM = "strom"                     # Stromkosten (auch Allgemeinstrom)
    HEIZUNG = "heizung"                 # Heizkosten, Heizstrom, Gas
    WARTUNG = "wartung"                 # Schornsteinfeger, Heizungswartung
    GRUND = "grund"                     # Grundsteuer
    HAUSGELD = "hausgeld"               # WEG-Hausgeld
    KOMMUNIKATION = "kommunikation"     # Kabel-TV, Gemeinschaftsantenne
    REINIGUNG = "reinigung"             # Treppenhausreinigung, Winterdienst
    SONSTIGES = "sonstiges"

class Zuordnung(StrEnum):
    HAUS = "haus"                       # Wird auf Parteien verteilt
    PARTEI = "partei"                   # Gehört nur einer Partei

class Betragsmodus(StrEnum):
    PAUSCHAL = "pauschal"               # Fester Betrag pro Periode
    VERBRAUCH = "verbrauch"             # Einheitspreis × Verbrauch

class Periodizitaet(StrEnum):
    MONATLICH = "monatlich"
    QUARTALSWEISE = "quartalsweise"
    HALBJAEHRLICH = "halbjaehrlich"
    JAEHRLICH = "jaehrlich"
    EINMALIG = "einmalig"

class Einheit(StrEnum):
    KUBIKMETER = "m3"                   # Wasser
    KWH = "kwh"                         # Strom, Gas
    LITER = "liter"                     # Öl

class Verteilung(StrEnum):
    DIREKT = "direkt"                   # 100 % an die zugeordnete Partei
    GLEICH = "gleich"                   # Kopfteilig auf alle aktiven Parteien
    FLAECHE = "flaeche"                 # Nach m²
    PERSONEN = "personen"               # Nach Personenzahl
    VERBRAUCH_SUBZAEHLER = "verbrauch"  # Nach gemessenem Einzelverbrauch
```

### Validierungs-Matrix

Nicht jede Kombination ist sinnvoll. Der Config Flow muss folgendes erzwingen:

| `zuordnung` | `betragsmodus` | `verteilung` | Sinnvoll? |
|---|---|---|---|
| PARTEI | PAUSCHAL | DIREKT | ✅ — klassischer Fall (z. B. Grundgebühr Strom OG) |
| PARTEI | VERBRAUCH | DIREKT | ✅ — z. B. individueller Stromzähler |
| PARTEI | * | GLEICH/FLAECHE/PERSONEN | ❌ — widersprüchlich |
| HAUS | PAUSCHAL | GLEICH/FLAECHE/PERSONEN | ✅ — z. B. Müll nach Personen |
| HAUS | PAUSCHAL | DIREKT | ❌ — widersprüchlich |
| HAUS | VERBRAUCH | FLAECHE/PERSONEN | ✅ — Wasser ohne Subzähler → nach Personen |
| HAUS | VERBRAUCH | VERBRAUCH_SUBZAEHLER | ✅ — Wasser mit Subzählern |
| HAUS | VERBRAUCH | GLEICH | ✅ — selten, aber möglich |

**Implementierung in `config_flow.py`:** Nach Auswahl von `zuordnung` werden die möglichen Werte für `betragsmodus` und `verteilung` auf die gültigen Kombinationen reduziert.

---

## `AdHocKosten`

Einmalige Kosten, die nicht in den regulären Subentries landen (z. B. spontane Reparatur). Via Service `hauskosten.add_einmalig`.

```python
class AdHocKosten(TypedDict):
    id: str
    bezeichnung: str
    kategorie: Kategorie
    betrag_eur: float
    datum: date
    zuordnung: Zuordnung
    zuordnung_partei_id: str | None
    verteilung: Verteilung
    bezahlt_am: date | None
    notiz: str | None
```

Persistenz: In der Storage-Datei des Entries, nicht als Subentry.

---

## `CoordinatorData`

Das Output-Format des Coordinators, Input für die Sensor-Platform.

```python
class CoordinatorData(TypedDict):
    computed_at: datetime
    jahr: int
    monat: int
    parteien: dict[str, ParteiResult]      # partei_id → result
    haus: HausResult

class ParteiResult(TypedDict):
    partei: Partei
    monat_aktuell_eur: float
    jahr_aktuell_eur: float
    jahr_budget_eur: float
    pro_kategorie_jahr_eur: dict[Kategorie, float]
    naechste_faelligkeit: date | None
    positionen: list[PositionAttribution]

class HausResult(TypedDict):
    jahr_budget_eur: float
    jahr_aktuell_eur: float
    pro_kategorie_jahr_eur: dict[Kategorie, float]

class PositionAttribution(TypedDict):
    kostenposition_id: str
    bezeichnung: str
    kategorie: Kategorie
    anteil_eur_jahr: float
    verteilschluessel_verwendet: Verteilung
    error: str | None    # wenn Berechnung fehlschlug
```

---

## Edge Cases, die das Modell abbildet

1. **Mieterwechsel mitten im Jahr** — via `bewohnt_ab` / `bewohnt_bis` pro Partei; Coordinator zeitgewichtet
2. **Saisonale Kosten (z. B. Heizung nur Okt–Apr)** — via `aktiv_ab` / `aktiv_bis` pro Kostenposition
3. **Leerstand** — eigene „Leerstand"-Partei anlegen (Kategorie `hinweis: "leerstand"`)
4. **Kosten nur eine Partei** — `zuordnung = PARTEI`, `verteilung = DIREKT`
5. **Verbrauch mit Subzählern** — `verbrauch_entities_pro_partei`
6. **Verbrauch ohne Subzähler** — Gesamt-Entity + `verteilung = PERSONEN`/`FLAECHE`

## Edge Cases, die das Modell **nicht** abbildet (bewusst)

- Indexanpassungen (User muss `betrag_eur` manuell aktualisieren)
- Mehrwertsteuer-Splits
- Cross-Year-Abrechnungen (z. B. Nachzahlung 2025 in 2026)
- Anteilige Jahresbeträge bei Mitte-des-Jahres-Start (wird per Zeitgewichtung durch Coordinator gelöst, aber nicht als eigenes Datum-Feld)

## Migrations-Strategie

Das Datenmodell wird sich ändern. Jede Breaking Change bekommt eine **Schema-Version** in `const.py` (`CONF_SCHEMA_VERSION`), und `async_migrate_entry` in `__init__.py` migriert alte Einträge.

**Regel:** Ein Schema, das in einer Release-Version live war, darf nie retroaktiv geändert werden. Änderung = neue Schema-Version + Migration.
