# ha-hauskosten

> **⚠️ Hinweis:** Diese Integration befindet sich in früher Entwicklung. Noch nicht für Produktivbetrieb geeignet. Folge dem Repo für Updates.

Home-Assistant-Custom-Integration für **faire Hauskosten-Verteilung in Mehrfamilienhäusern**. Verteilt fixe und verbrauchsbasierte Kosten (Versicherung, Müll, Wasser, Heizung, …) auf Wohnparteien nach Quadratmetern, Personenzahl oder individuellem Verbrauch.

---

## 💖 Unterstütze dieses Projekt

Wenn dir `ha-hauskosten` hilft, unterstütze die Weiterentwicklung:

🫶 [**YouTube-Kanalmitglied werden**](https://youtube.com/@simon42/join) — bevorzugter Support
☕ [**Buy Me A Coffee**](https://www.buymeacoffee.com/simon42official)

---

## ✨ Features

### 🏠 Multi-Parteien-Setup

- Beliebig viele Wohnparteien mit individuellen Stammdaten
- Fläche in m² und Personenzahl pro Partei
- Historie via Bewohnungszeitraum (`bewohnt_ab` / `bewohnt_bis`) — Mieterwechsel werden sauber abgebildet

### 💰 Flexible Kostenmodelle

- **Pauschalkosten** (monatlich, quartalsweise, halbjährlich, jährlich, einmalig)
- **Verbrauchsbasierte Kosten** mit Entity-Referenz (Wasserzähler, Stromzähler)
- Optionale monatliche Grundgebühr zusätzlich zum Verbrauchstarif

### ⚖️ Vier Verteilungsschlüssel

- **Direkt** — 100 % an eine Partei (z. B. individueller Stromzähler)
- **Gleich** — kopfteilig auf alle Parteien
- **Fläche** — proportional zur Wohnfläche (m²)
- **Personen** — proportional zur Personenzahl
- **Verbrauch (Subzähler)** — nach gemessenem individuellen Verbrauch

### 📊 Automatische Sensoren

Pro Partei entstehen Sensoren für monatliche und jährliche Kosten, nächste Fälligkeit, sowie eine Aufschlüsselung pro Kategorie.

### 🗓️ Saisonale Kosten

Kostenpositionen können zeitlich begrenzt sein (z. B. Heizkosten nur Oktober–April).

---

## 📦 Installation

### Via HACS (empfohlen)

> TODO: HACS-Default-Submission geplant für v1.0.0. Bis dahin als Custom Repository:

1. HACS → Integrationen → ⋮ → Benutzerdefinierte Repositories
2. Repository: `https://github.com/TheRealSimon42/ha-hauskosten`
3. Kategorie: Integration
4. Hinzufügen → Installieren → Home Assistant neu starten

### Manuell

1. Kopiere den Ordner `custom_components/hauskosten` nach `/config/custom_components/hauskosten`
2. Home Assistant neu starten

---

## 🖥️ Erste Schritte

1. **Einstellungen** → **Geräte & Dienste** → **Integration hinzufügen**
2. Suche nach „Hauskosten" und folge dem Einrichtungs-Assistenten
3. Lege für jede Wohnpartei einen Subentry „Partei" an
4. Lege für jede Kostenposition einen Subentry „Kostenposition" an

> 📸 Screenshots folgen in v0.2.0.

---

## ⚙️ Konfiguration

Die Konfiguration erfolgt vollständig über das Home-Assistant-Frontend. Keine YAML-Editierung notwendig.

### Subentry-Typen

**Partei:**

| Feld | Typ | Beschreibung |
|---|---|---|
| Name | Text | z. B. „OG (Simon)" |
| Fläche | m² | Wohnfläche in Quadratmetern |
| Personen | Zahl | Anzahl der Bewohner |
| Bewohnt ab | Datum | Einzugsdatum |
| Bewohnt bis | Datum | optional, leer = aktuell bewohnt |

**Kostenposition:**

| Feld | Typ | Beschreibung |
|---|---|---|
| Bezeichnung | Text | z. B. „Gebäudeversicherung" |
| Kategorie | Auswahl | Versicherung, Müll, Wasser, … |
| Zuordnung | Auswahl | Haus (auf Parteien verteilt) oder Partei (nur eine) |
| Betragsmodus | Auswahl | Pauschal oder verbrauchsbasiert |
| Verteilung | Auswahl | Gleich / Fläche / Personen / Verbrauch / Direkt |

Die vollständige Validierungsmatrix findet sich in [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md#validierungs-matrix).

---

## 📊 Erzeugte Entities

Pro Partei `{p}` und Kategorie `{k}`:

| Entity | Beschreibung |
|---|---|
| `sensor.hauskosten_{p}_monat_aktuell` | Kosten laufender Monat (€) |
| `sensor.hauskosten_{p}_jahr_aktuell` | Kosten laufendes Jahr (€) |
| `sensor.hauskosten_{p}_jahr_budget` | Jahresbudget (€) |
| `sensor.hauskosten_{p}_naechste_faelligkeit` | Datum nächster Zahlung |
| `sensor.hauskosten_{p}_{k}_jahr` | Kategorie-Kosten im Jahr |

Plus Haus-Aggregate: `sensor.hauskosten_haus_jahr_gesamt` usw.

---

## 🎯 Service-Calls

> 📝 Services werden ab v0.3.0 verfügbar sein.

- `hauskosten.add_einmalig` — Ad-hoc-Kostenposition hinzufügen (z. B. spontane Reparatur)
- `hauskosten.mark_paid` — Position als bezahlt markieren
- `hauskosten.reset_year` — Jahreswechsel-Reset

---

## 🤖 Projekt-Kontext für KI-Assistenten

Dieses Projekt ist eine Home-Assistant-Custom-Integration, entwickelt für faire Hauskosten-Verteilung in Mehrfamilienhäusern. Die Codebasis ist in Python 3.13+ strukturiert und folgt dem HA-Core-Integration-Standard.

- **Entry-Point:** `custom_components/hauskosten/__init__.py`
- **Config Flow:** `config_flow.py` mit Subentries für Parteien und Kostenpositionen
- **Coordinator:** `coordinator.py` (DataUpdateCoordinator-Pattern)
- **Pure Logic:** `distribution.py` und `calculations.py` (ohne HA-Abhängigkeiten, 100 % Test-Coverage)
- **Sensoren:** `sensor.py`
- **Storage:** `storage.py` (HA Store API)

Für Änderungen: Siehe [`AGENTS.md`](AGENTS.md) und [`docs/ONBOARDING.md`](docs/ONBOARDING.md). Coding-Standards siehe [`docs/STANDARDS.md`](docs/STANDARDS.md). HACS-kompatibel ab Version 0.1.0.

---

## 🏗️ Architektur (Kurzüberblick)

```
User Input (Config Flow Subentries + Storage)
         ↓
  DataUpdateCoordinator
    ├── distribution.py  (pure logic, 100% Test-Coverage)
    └── calculations.py  (pure logic, 100% Test-Coverage)
         ↓
  Sensor Platform
    ├── pro Partei
    ├── pro Kategorie
    └── Haus-Gesamt
```

Details siehe [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## 🤝 Contributing

Contributions sind willkommen! Bitte lies vorher:

- [`AGENTS.md`](AGENTS.md) — Projekt-Grundregeln
- [`docs/ONBOARDING.md`](docs/ONBOARDING.md) — Wo fange ich an?
- [`docs/STANDARDS.md`](docs/STANDARDS.md) — Coding-Standards

### Setup

```bash
git clone https://github.com/TheRealSimon42/ha-hauskosten.git
cd ha-hauskosten
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
pre-commit install
pytest
```

---

## 📋 Roadmap

- **v0.1.0** — MVP: Pauschal-Kosten, Verteilung nach qm/Personen/gleich
- **v0.2.0** — Verbrauchsbasierte Kosten mit Entity-Referenz
- **v0.3.0** — Service-Actions, Fälligkeits-Reminder
- **v0.4.0** — Optional: Frontend-Card
- **v1.0.0** — HACS-Default-Submission, Quality-Scale Silver

---

## 🐛 Bekannte Probleme & Limitationen

- Keine rechtsverbindliche Abrechnung (BetrKV / HeizkostenV) — wir tracken, wir rechnen nicht ab
- Indexanpassungen (z. B. jährlicher Versicherungs-Anstieg) müssen manuell gepflegt werden
- Multi-Property-Szenarien (mehrere Häuser) laufen als separate Config-Entries

---

## 📄 Lizenz

**Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0)**

### Du darfst

- ✅ **Teilen** — Das Material kopieren und weiterverbreiten
- ✅ **Bearbeiten** — Das Material remixen, verändern und darauf aufbauen

### Unter folgenden Bedingungen

- 📝 **Namensnennung** — Angemessene Urheber-Nennung
- 🚫 **Nicht kommerziell** — Keine kommerzielle Nutzung
- 🔄 **Weitergabe unter gleichen Bedingungen** — Bei Veränderungen unter gleicher Lizenz

### 💼 Kommerzielle Nutzung

Interessiert an kommerzieller Nutzung? Kontakt über <https://www.simon42.com/contact/>.

Siehe [`LICENSE`](LICENSE) für vollständige Details.

---

## 🙏 Credits

- Home-Assistant-Community für Inspiration und Feedback
- Alle Contributor und Tester

---

## 📞 Support & Kontakt

- 🐛 **[GitHub Issues](https://github.com/TheRealSimon42/ha-hauskosten/issues)**
- 💬 **[simon42 Community](https://community.simon42.com/)**
- 🎥 **[YouTube-Kanal](https://youtube.com/@simon42)**

---

**Entwickelt mit ❤️ für die Home-Assistant-Community**
