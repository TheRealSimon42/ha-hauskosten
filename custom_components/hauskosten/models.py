"""Datenmodell fuer ha-hauskosten.

Autoritative Spezifikation: docs/DATA_MODEL.md.
Aenderungen hier erfordern eine Aktualisierung der Spec UND ggf. eine Migration
in __init__.py (siehe CONF_SCHEMA_VERSION in const.py).

Dieser Stub wird vom integration-architect Sub-Agent ausgefuellt.
"""

from __future__ import annotations

from datetime import date, datetime  # noqa: TC003
from enum import StrEnum
from typing import TypedDict


class Kategorie(StrEnum):
    """Kostenkategorie (siehe docs/DATA_MODEL.md)."""

    VERSICHERUNG = "versicherung"
    MUELL = "muell"
    WASSER = "wasser"
    ABWASSER = "abwasser"
    STROM = "strom"
    HEIZUNG = "heizung"
    WARTUNG = "wartung"
    GRUND = "grund"
    HAUSGELD = "hausgeld"
    KOMMUNIKATION = "kommunikation"
    REINIGUNG = "reinigung"
    SONSTIGES = "sonstiges"


class Zuordnung(StrEnum):
    """Wem gehoert die Kostenposition?"""

    HAUS = "haus"
    PARTEI = "partei"


class Betragsmodus(StrEnum):
    """Wie wird der Betrag ermittelt?"""

    PAUSCHAL = "pauschal"
    VERBRAUCH = "verbrauch"


class Periodizitaet(StrEnum):
    """Zyklus fuer pauschale Kosten."""

    MONATLICH = "monatlich"
    QUARTALSWEISE = "quartalsweise"
    HALBJAEHRLICH = "halbjaehrlich"
    JAEHRLICH = "jaehrlich"
    EINMALIG = "einmalig"


class Einheit(StrEnum):
    """Einheit fuer verbrauchsbasierte Kosten."""

    KUBIKMETER = "m3"
    KWH = "kwh"
    LITER = "liter"


class Verteilung(StrEnum):
    """Verteilungs-Schluessel auf Parteien."""

    DIREKT = "direkt"
    GLEICH = "gleich"
    FLAECHE = "flaeche"
    PERSONEN = "personen"
    VERBRAUCH_SUBZAEHLER = "verbrauch"


class Partei(TypedDict):
    """Eine Wohneinheit im Haus.

    Siehe docs/DATA_MODEL.md fuer Validierungsregeln.
    """

    id: str
    name: str
    flaeche_qm: float
    personen: int
    bewohnt_ab: date
    bewohnt_bis: date | None
    hinweis: str | None


class Kostenposition(TypedDict):
    """Eine einzelne Kostenquelle.

    Siehe docs/DATA_MODEL.md fuer die Validierungs-Matrix.
    """

    id: str
    bezeichnung: str
    kategorie: Kategorie
    zuordnung: Zuordnung
    zuordnung_partei_id: str | None
    betragsmodus: Betragsmodus
    # Pauschal:
    betrag_eur: float | None
    periodizitaet: Periodizitaet | None
    faelligkeit: date | None
    # Verbrauch:
    verbrauchs_entity: str | None
    einheitspreis_eur: float | None
    einheit: Einheit | None
    grundgebuehr_eur_monat: float | None
    # Verteilung:
    verteilung: Verteilung
    verbrauch_entities_pro_partei: dict[str, str] | None
    # Saison:
    aktiv_ab: date | None
    aktiv_bis: date | None
    # Meta:
    notiz: str | None


class AdHocKosten(TypedDict):
    """Einmalige Kostenposition ausserhalb der regulaeren Subentries.

    Gespeichert in storage.py, nicht als Subentry.
    """

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


class PositionAttribution(TypedDict):
    """Ergebnis der Verteilung einer Kostenposition fuer eine Partei."""

    kostenposition_id: str
    bezeichnung: str
    kategorie: Kategorie
    anteil_eur_jahr: float
    verteilschluessel_verwendet: Verteilung
    error: str | None


class ParteiResult(TypedDict):
    """Aggregierte Kosten einer Partei im Coordinator-Output."""

    partei: Partei
    monat_aktuell_eur: float
    jahr_aktuell_eur: float
    jahr_budget_eur: float
    pro_kategorie_jahr_eur: dict[Kategorie, float]
    naechste_faelligkeit: date | None
    positionen: list[PositionAttribution]


class HausResult(TypedDict):
    """Aggregierte Haus-Kosten im Coordinator-Output."""

    jahr_budget_eur: float
    jahr_aktuell_eur: float
    pro_kategorie_jahr_eur: dict[Kategorie, float]


class CoordinatorData(TypedDict):
    """Das Output-Format des Coordinators.

    Diese Struktur wird von allen Sensor-Entities konsumiert.
    """

    computed_at: datetime
    jahr: int
    monat: int
    parteien: dict[str, ParteiResult]
    haus: HausResult
