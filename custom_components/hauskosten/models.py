"""Data model for ha-hauskosten.

Authoritative spec: ``docs/DATA_MODEL.md``. Every TypedDict and StrEnum in this
module mirrors a section of that document one-to-one. Whenever the code and
the spec disagree, the spec is the plan and the code is the bug -- unless the
``integration-architect`` has explicitly signed off on a spec change, in which
case ``CONF_SCHEMA_VERSION`` in :mod:`.const` must be bumped and a migration
must be added to :func:`custom_components.hauskosten.async_migrate_entry`.

This module is **free of Home Assistant imports**. TypedDicts are pure Python
and must stay that way so distribution / calculations / storage modules can
import them without pulling in the HA runtime.

Conventions (per ``docs/DATA_MODEL.md``):

* Money amounts use ``float`` in Euro. Rounding happens at the output edges
  (sensor state, service response), not in the model.
* IDs are ``str`` values generated via ``uuid.uuid4()`` when a record is
  created. This module does not generate IDs itself.
* Date fields use :class:`datetime.date` (no timezone, no time component).
* Enum values are ``StrEnum`` members whose *values* are lowercase
  snake-case strings -- these values are what gets serialised to the config
  subentry / store, so they are part of the persisted schema.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import TypedDict

__all__ = [
    "AdHocKosten",
    "Betragsmodus",
    "CoordinatorData",
    "Einheit",
    "HausResult",
    "Kategorie",
    "Kostenposition",
    "Partei",
    "ParteiResult",
    "Periodizitaet",
    "PositionAttribution",
    "Verteilung",
    "Zuordnung",
]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Kategorie(StrEnum):
    """Cost category used for grouping and per-category sensors.

    Values are persisted strings, so any rename is a breaking schema change.
    See ``docs/DATA_MODEL.md`` for the canonical list.
    """

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
    """Who the Kostenposition belongs to.

    ``HAUS`` means the cost is spread across parties via a Verteilung key;
    ``PARTEI`` means the cost belongs to exactly one party (DIREKT allocation
    only).
    """

    HAUS = "haus"
    PARTEI = "partei"


class Betragsmodus(StrEnum):
    """How the amount of a Kostenposition is determined.

    ``PAUSCHAL`` uses a fixed ``betrag_eur`` per ``periodizitaet``;
    ``VERBRAUCH`` multiplies a unit price with a meter reading.
    """

    PAUSCHAL = "pauschal"
    VERBRAUCH = "verbrauch"


class Periodizitaet(StrEnum):
    """Recurrence cadence for pauschal cost items.

    ``EINMALIG`` is a one-off; it does not annualise (see
    :func:`.calculations.annualize`) and uses ``start`` as its only candidate
    due date.
    """

    MONATLICH = "monatlich"
    QUARTALSWEISE = "quartalsweise"
    HALBJAEHRLICH = "halbjaehrlich"
    JAEHRLICH = "jaehrlich"
    EINMALIG = "einmalig"


class Einheit(StrEnum):
    """Unit of measurement for consumption-based cost items.

    The value is the short string stored alongside the Kostenposition; the
    sensor platform maps these to HA unit-of-measurement constants at the
    edge, so this enum stays free of HA imports.
    """

    KUBIKMETER = "m3"
    KWH = "kwh"
    LITER = "liter"


class Verteilung(StrEnum):
    """Distribution key used by :func:`.distribution.allocate`.

    Note:
        ``VERBRAUCH_SUBZAEHLER`` intentionally has the **value** ``"verbrauch"``
        (not ``"verbrauch_subzaehler"``) to match the canonical spec in
        ``docs/DATA_MODEL.md``. The member name carries the longer,
        unambiguous identifier for Python-level callers.
    """

    DIREKT = "direkt"
    GLEICH = "gleich"
    FLAECHE = "flaeche"
    PERSONEN = "personen"
    VERBRAUCH_SUBZAEHLER = "verbrauch"


# ---------------------------------------------------------------------------
# Persisted records (subentries + ad-hoc storage)
# ---------------------------------------------------------------------------


class Partei(TypedDict):
    """A single residential unit in the house (persisted as a subentry).

    See ``docs/DATA_MODEL.md`` for validation rules enforced by the config
    flow: name uniqueness and length, m2 bounds, person count bounds, and
    ``bewohnt_ab <= bewohnt_bis``.

    Fields:
        id: UUID generated on creation; stable across renames.
        name: Display name (1-50 chars, unique per config entry).
        flaeche_qm: Floor area in square metres (> 0, < 1000).
        personen: Headcount for personen-based distribution (0-20).
        bewohnt_ab: Inclusive start of tenancy / ownership.
        bewohnt_bis: Inclusive end of tenancy, or ``None`` for open-ended.
        hinweis: Free-text note, e.g. ``"leerstand"`` for vacant units.
    """

    id: str
    name: str
    flaeche_qm: float
    personen: int
    bewohnt_ab: date
    bewohnt_bis: date | None
    hinweis: str | None


class Kostenposition(TypedDict):
    """A single cost line item (persisted as a subentry).

    The validation matrix in ``docs/DATA_MODEL.md`` constrains which
    combinations of ``zuordnung`` / ``betragsmodus`` / ``verteilung`` are
    semantically valid; the config flow is responsible for enforcing that
    matrix, this TypedDict only describes the shape.

    Fields:
        id: UUID generated on creation.
        bezeichnung: Human-readable label.
        kategorie: Cost category for grouping / per-category sensors.
        zuordnung: ``HAUS`` (spread) or ``PARTEI`` (single-party).
        zuordnung_partei_id: Target party id when ``zuordnung == PARTEI``.
        betragsmodus: ``PAUSCHAL`` (fixed) or ``VERBRAUCH`` (metered).
        betrag_eur: Fixed amount per ``periodizitaet`` (pauschal only).
        periodizitaet: Cadence of the pauschal amount.
        faelligkeit: First due date, also the anchor for recurrences.
        verbrauchs_entity: HA entity id of the main consumption sensor
            (verbrauch only).
        einheitspreis_eur: Price per unit, e.g. EUR/m3 (verbrauch only).
        einheit: Unit the price is expressed in (verbrauch only).
        grundgebuehr_eur_monat: Optional monthly base fee (verbrauch only).
        verteilung: Distribution key used by
            :func:`.distribution.allocate`.
        verbrauch_entities_pro_partei: Map ``{partei_id: entity_id}`` used
            when ``verteilung == VERBRAUCH_SUBZAEHLER``.
        aktiv_ab: Optional start of the seasonal activity window.
        aktiv_bis: Optional end of the seasonal activity window.
        notiz: Free-text note for the user.
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
    """A one-off cost added via the ``hauskosten.add_einmalig`` service.

    Lives in the per-entry store (``homeassistant.helpers.storage.Store``),
    **not** as a subentry -- subentries are for user-curated master data,
    whereas ad-hoc entries are runtime events (a handyman bill, a one-time
    repair). Persisted alongside ``bezahlt_am`` timestamps managed by
    ``hauskosten.mark_paid``.

    Fields:
        id: UUID generated on creation.
        bezeichnung: Human-readable label.
        kategorie: Cost category for grouping.
        betrag_eur: The actual amount paid (gross).
        datum: Date the cost was incurred.
        zuordnung: Whether the cost is split (HAUS) or for one party (PARTEI).
        zuordnung_partei_id: Target party id when ``zuordnung == PARTEI``.
        verteilung: Distribution key for HAUS-assigned ad-hoc costs.
        bezahlt_am: Date the cost was paid, or ``None`` if outstanding.
        notiz: Free-text note.
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


# ---------------------------------------------------------------------------
# Coordinator output (consumed by the sensor platform)
# ---------------------------------------------------------------------------


class PositionAttribution(TypedDict):
    """Allocation of one Kostenposition to one party for a full year.

    Produced by the coordinator when flattening the output of
    :func:`.distribution.allocate` into per-party line items. Exposed on
    sensor ``extra_state_attributes`` so dashboards can drill down into the
    sources of a party's total.

    Fields:
        kostenposition_id: ID of the source Kostenposition.
        bezeichnung: Label of the Kostenposition (denormalised for ease of
            consumption by the sensor platform).
        kategorie: Category of the Kostenposition (denormalised).
        anteil_eur_jahr: This party's yearly share in Euro (rounded to 2
            decimals by the coordinator).
        verteilschluessel_verwendet: Which distribution key actually
            produced this share (useful when fallbacks kick in).
        error: Non-``None`` message when the allocation failed for this
            party -- the sensor shows the error rather than a misleading 0 EUR.
    """

    kostenposition_id: str
    bezeichnung: str
    kategorie: Kategorie
    anteil_eur_jahr: float
    verteilschluessel_verwendet: Verteilung
    error: str | None


class ParteiResult(TypedDict):
    """Aggregated cost result for one party in the coordinator output.

    Fields:
        partei: The source Partei record (denormalised to avoid double
            lookups in the sensor platform).
        monat_aktuell_eur: Costs attributable to the current month (EUR).
        jahr_aktuell_eur: Costs attributable year-to-date (EUR).
        jahr_budget_eur: Expected total for the full year (EUR).
        pro_kategorie_jahr_eur: Per-category yearly totals (EUR).
        naechste_faelligkeit: Earliest upcoming due date across this
            party's positions, or ``None`` if no recurring positions.
        positionen: Flat list of per-position attributions for drill-down.
    """

    partei: Partei
    monat_aktuell_eur: float
    jahr_aktuell_eur: float
    jahr_budget_eur: float
    pro_kategorie_jahr_eur: dict[Kategorie, float]
    naechste_faelligkeit: date | None
    positionen: list[PositionAttribution]


class HausResult(TypedDict):
    """Aggregated house-wide totals in the coordinator output.

    Fields:
        jahr_budget_eur: Sum of all parties' yearly budgets (EUR).
        jahr_aktuell_eur: House-wide year-to-date total (EUR).
        pro_kategorie_jahr_eur: House-wide per-category yearly totals (EUR).
    """

    jahr_budget_eur: float
    jahr_aktuell_eur: float
    pro_kategorie_jahr_eur: dict[Kategorie, float]


class CoordinatorData(TypedDict):
    """Top-level output of :class:`HauskostenCoordinator`.

    Consumed by the sensor platform via
    ``DataUpdateCoordinator[CoordinatorData]``. The shape is stable across
    HA restarts; any field change here is a breaking change for downstream
    dashboards and custom cards.

    Fields:
        computed_at: Timezone-aware timestamp of the last compute cycle.
        jahr: The accounting year the totals refer to (e.g. 2026).
        monat: The accounting month the ``monat_aktuell_eur`` values refer
            to (1-12).
        parteien: Mapping ``{partei_id: ParteiResult}`` for every party
            known to the entry.
        haus: Aggregated house-wide totals.
    """

    computed_at: datetime
    jahr: int
    monat: int
    parteien: dict[str, ParteiResult]
    haus: HausResult
