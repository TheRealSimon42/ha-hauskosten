# Verteilungs-Algorithmen

Diese Spezifikation ist die einzige Quelle der Wahrheit für `distribution.py`. Jede Formel hier hat einen korrespondierenden Unit-Test in `tests/test_distribution.py`.

## Gemeinsame Signatur

```python
def allocate(
    betrag_eur_jahr: float,
    parteien: list[Partei],
    *,
    key: Verteilung,
    stichtag: date,
    extra: dict | None = None,
) -> dict[str, float]:
    """
    Returns {partei_id: anteil_eur_jahr}.

    Die Summe der Werte entspricht exakt betrag_eur_jahr (bis auf Rundungen auf
    2 Nachkommastellen — Rundungsrest landet bei der ersten Partei).
    """
```

**Wichtig — Zeitgewichtung:** Eine Partei, die nur einen Teil des Jahres aktiv war (via `bewohnt_ab` / `bewohnt_bis`), bekommt ihren Anteil **vorher** zeitgewichtet. Das passiert NICHT in `allocate()`, sondern im Coordinator als Vorverarbeitung. `allocate()` bekommt schon einen gewichteten `effektive_tage`-Wert pro Partei mitgegeben (im `extra`-Dict).

---

## 1. `DIREKT` — 100 % an eine Partei

Trivialfall. `betrag_eur_jahr` geht komplett an `zuordnung_partei_id`.

```
anteil(p_i) = betrag_eur_jahr  wenn i == zuordnung_partei_id, sonst 0
```

**Nur gültig bei `zuordnung = PARTEI`.**

**Edge Case:** Zugeordnete Partei existiert nicht mehr → `ValueError`, Coordinator markiert Position als Error.

---

## 2. `GLEICH` — Kopfteilig auf alle aktiven Parteien

```
n = Anzahl aktiver Parteien am Stichtag
anteil(p_i) = betrag_eur_jahr / n  für alle aktiven p_i
```

**Aktiv am Stichtag** = `bewohnt_ab ≤ stichtag ≤ (bewohnt_bis or ∞)`.

**Edge Case `n == 0`:** `ValueError("keine aktiven parteien")`. Sollte in der Praxis nie eintreten, weil mindestens eine Partei existieren muss.

---

## 3. `FLAECHE` — Nach Wohnfläche

```
gesamt_qm = Σ flaeche_qm(p_i)  für alle aktiven p_i
anteil(p_i) = betrag_eur_jahr * (flaeche_qm(p_i) / gesamt_qm)
```

**Beispiel:**
- Haus-Versicherung 450 €/a
- OG 85 m², DG 65 m²
- gesamt = 150 m²
- OG-Anteil = 450 × (85/150) = **255 €**
- DG-Anteil = 450 × (65/150) = **195 €**

---

## 4. `PERSONEN` — Nach Personenzahl

```
gesamt_p = Σ personen(p_i)  für alle aktiven p_i
anteil(p_i) = betrag_eur_jahr * (personen(p_i) / gesamt_p)
```

**Beispiel:**
- Müllgebühr 240 €/a
- OG 2 Personen, DG 1 Person
- gesamt = 3
- OG-Anteil = 240 × (2/3) = **160 €**
- DG-Anteil = 240 × (1/3) = **80 €**

**Edge Case `personen = 0` (Leerstand) in einer Partei bei gesamt_p > 0:** Partei bekommt Anteil 0, Rest wird auf andere verteilt.

**Edge Case `gesamt_p == 0` (alle Leerstand):** `ValueError`. Alternative: Fallback auf `FLAECHE`. **Entscheidung MVP:** Hard Fail, User muss Positionen manuell anpassen.

---

## 5. `VERBRAUCH_SUBZAEHLER` — Nach gemessenem Verbrauch

Nur sinnvoll, wenn `betragsmodus = VERBRAUCH` und pro Partei ein eigener Zähler existiert.

```
verbrauch_i = Messwert von verbrauch_entities_pro_partei[p_i] im Zeitraum
gesamt_v = Σ verbrauch_i
anteil(p_i) = betrag_eur_jahr * (verbrauch_i / gesamt_v)
```

**Wichtig:** Der Coordinator muss den **periodengenauen** Verbrauch ermitteln — also Differenz zwischen dem Zählerstand am Anfang und am Ende des Abrechnungszeitraums. Das ist nicht trivial, deshalb empfehlen wir Utility-Meter-Sensoren, die vom User extern eingerichtet werden.

**Edge Case fehlender Zählerstand:** Position wird als `error="verbrauchs_entity missing"` markiert.

---

## 6. Kombi-Fall: Verbrauch ohne Subzähler

Bei `betragsmodus = VERBRAUCH` + `verteilung ∈ {FLAECHE, PERSONEN, GLEICH}`:

1. **Zuerst** den Gesamt-Euro-Betrag aus Verbrauch × Einheitspreis berechnen (in `calculations.py`)
2. **Dann** diesen Betrag mit dem gewählten Schlüssel verteilen (`FLAECHE` / `PERSONEN` / `GLEICH`)

Das ist der typische Fall „Frischwasser ohne Zwischenzähler — Verteilung nach Personen".

---

## Zeitgewichtung bei Teiljahres-Bewohnung

Wenn eine Partei nur einen Teil des Jahres aktiv war, wird ihr Anteil im Verteilschlüssel gewichtet.

**Algorithmus:**

```python
def effektive_tage(partei: Partei, jahr: int) -> int:
    """Anzahl Tage, an denen die Partei im Zielperiode aktiv war."""
    jahr_start = date(jahr, 1, 1)
    jahr_ende = date(jahr, 12, 31)
    start = max(partei.bewohnt_ab, jahr_start)
    ende = min(partei.bewohnt_bis or jahr_ende, jahr_ende)
    return max(0, (ende - start).days + 1)
```

**Eingang in die Formel:** Statt `n`, `gesamt_qm`, `gesamt_p` werden **tage-gewichtete** Summen verwendet:

```
gewichteter_schluessel_i = schluessel_i * (effektive_tage_i / 365)
gesamt_gewichtet = Σ gewichteter_schluessel_i
anteil(p_i) = betrag * (gewichteter_schluessel_i / gesamt_gewichtet)
```

**Beispiel — Mieterwechsel:**
- Haus-Versicherung 450 €/a, Verteilung `FLAECHE`
- OG 85 m², ganzes Jahr → 85 × (365/365) = 85
- DG_alt 65 m², Jan–Juni (181 Tage) → 65 × (181/365) = 32.23
- DG_neu 65 m², Juli–Dez (184 Tage) → 65 × (184/365) = 32.77
- gesamt_gewichtet = 150
- OG = 450 × (85/150) = **255 €**
- DG_alt = 450 × (32.23/150) = **96.69 €**
- DG_neu = 450 × (32.77/150) = **98.31 €**

**Note:** Der Ansatz setzt voraus, dass die Kostenposition ganzjährig aktiv ist. Bei saisonalen Kosten (`aktiv_ab` / `aktiv_bis`) muss der gleiche Algorithmus zusätzlich auf Positionsebene laufen — also effektive Tage der **Position** × effektive Tage der **Partei**, geschnitten.

---

## Rundungsregel

Alle Zwischenwerte werden mit voller `float`-Präzision gerechnet. **Nur beim finalen `round(x, 2)`** wird gerundet. Der Rundungsrest wird auf die Partei mit dem höchsten Rohanteil aufgeschlagen, damit die Summe exakt bleibt.

```python
def distribute_with_rounding_fix(
    betrag: float,
    weights: dict[str, float],
) -> dict[str, float]:
    total_weight = sum(weights.values())
    raw = {pid: betrag * w / total_weight for pid, w in weights.items()}
    rounded = {pid: round(v, 2) for pid, v in raw.items()}
    diff = round(betrag - sum(rounded.values()), 2)
    if diff != 0:
        # Rundungsrest auf Partei mit höchstem Rohanteil
        largest = max(raw, key=raw.get)
        rounded[largest] = round(rounded[largest] + diff, 2)
    return rounded
```

## Testing

Jeder Verteilschlüssel hat in `tests/test_distribution.py` mindestens:

1. **Happy Path** (2 gleich lange Parteien, simple Werte)
2. **Mieterwechsel** (Zeitgewichtung)
3. **Leerstand-Partei** (0 Personen)
4. **Rundungsfall** (Betrag, der nicht exakt teilbar ist)
5. **Error-Fall** (leere Parteien-Liste, fehlender Zähler)

Siehe `.claude/agents/distribution-logic.md` für konkrete Test-Templates.
