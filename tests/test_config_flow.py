"""Tests for custom_components.hauskosten.config_flow.

Covers:
* Main user flow (Haus anlegen): happy path, duplicate abort, name validation.
* Partei subentry flow: create happy path, duplicate name, out-of-range values,
  invalid date range, reconfigure with pre-fill.
* Kostenposition subentry flow (multi-step): happy paths for all five valid
  combinations of (zuordnung, betragsmodus, verteilung) per the validation
  matrix, invalid-combination rejection, reconfigure with pre-fill and a
  successful field update, and sub-meter branch for VERBRAUCH_SUBZAEHLER.

The tests rely on ``pytest-homeassistant-custom-component`` for ``hass``,
``MockConfigEntry`` and the config-flow driver.
"""

from __future__ import annotations

from datetime import date
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigSubentry
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from custom_components.hauskosten.config_flow import (
    _ALLOWED_VERTEILUNGEN,
    _allowed_verteilungen,
    _is_valid_combination,
    _validate_details_input,
    _validate_partei_input,
)
from custom_components.hauskosten.const import (
    CONF_ABRECHNUNGSZEITRAUM_DAUER_MONATE,
    CONF_ABRECHNUNGSZEITRAUM_START,
    CONF_AKTIV_AB,
    CONF_AKTIV_BIS,
    CONF_BETRAG_EUR,
    CONF_BETRAGSMODUS,
    CONF_BEWOHNT_AB,
    CONF_BEWOHNT_BIS,
    CONF_BEZEICHNUNG,
    CONF_EINHEIT,
    CONF_EINHEITSPREIS_EUR,
    CONF_FAELLIGKEIT,
    CONF_FLAECHE_QM,
    CONF_GRUNDGEBUEHR_EUR_MONAT,
    CONF_HAUS_NAME,
    CONF_HINWEIS,
    CONF_KATEGORIE,
    CONF_MONATLICHER_ABSCHLAG_EUR,
    CONF_NAME,
    CONF_NOTIZ,
    CONF_PERIODIZITAET,
    CONF_PERSONEN,
    CONF_VERBRAUCH_ENTITIES_PRO_PARTEI,
    CONF_VERBRAUCHS_ENTITY,
    CONF_VERTEILUNG,
    CONF_ZUORDNUNG,
    CONF_ZUORDNUNG_PARTEI_ID,
    DOMAIN,
    SUBENTRY_KOSTENPOSITION,
    SUBENTRY_PARTEI,
)
from custom_components.hauskosten.models import (
    Betragsmodus,
    Einheit,
    Kategorie,
    Periodizitaet,
    Verteilung,
    Zuordnung,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _partei_subentry(
    *,
    subentry_id: str = "partei-og",
    name: str = "OG (Simon)",
    flaeche_qm: float = 85.0,
    personen: int = 2,
    bewohnt_ab: str = "2020-01-01",
    bewohnt_bis: str | None = None,
    hinweis: str | None = None,
) -> ConfigSubentry:
    data: dict[str, Any] = {
        CONF_NAME: name,
        CONF_FLAECHE_QM: flaeche_qm,
        CONF_PERSONEN: personen,
        CONF_BEWOHNT_AB: bewohnt_ab,
        CONF_BEWOHNT_BIS: bewohnt_bis,
        CONF_HINWEIS: hinweis,
    }
    return ConfigSubentry(
        data=MappingProxyType(data),
        subentry_id=subentry_id,
        subentry_type=SUBENTRY_PARTEI,
        title=name,
        unique_id=None,
    )


def _kp_subentry(
    *,
    subentry_id: str = "kp-1",
    bezeichnung: str = "Gebaeudeversicherung",
    kategorie: str = Kategorie.VERSICHERUNG.value,
    zuordnung: str = Zuordnung.HAUS.value,
    zuordnung_partei_id: str | None = None,
    betragsmodus: str = Betragsmodus.PAUSCHAL.value,
    betrag_eur: float | None = 450.0,
    periodizitaet: str | None = Periodizitaet.JAEHRLICH.value,
    faelligkeit: str | None = "2026-03-15",
    verbrauchs_entity: str | None = None,
    einheitspreis_eur: float | None = None,
    einheit: str | None = None,
    grundgebuehr_eur_monat: float | None = None,
    verteilung: str = Verteilung.FLAECHE.value,
    verbrauch_entities_pro_partei: dict[str, str] | None = None,
    aktiv_ab: str | None = None,
    aktiv_bis: str | None = None,
    notiz: str | None = None,
) -> ConfigSubentry:
    data: dict[str, Any] = {
        CONF_BEZEICHNUNG: bezeichnung,
        CONF_KATEGORIE: kategorie,
        CONF_ZUORDNUNG: zuordnung,
        CONF_ZUORDNUNG_PARTEI_ID: zuordnung_partei_id,
        CONF_BETRAGSMODUS: betragsmodus,
        CONF_BETRAG_EUR: betrag_eur,
        CONF_PERIODIZITAET: periodizitaet,
        CONF_FAELLIGKEIT: faelligkeit,
        CONF_VERBRAUCHS_ENTITY: verbrauchs_entity,
        CONF_EINHEITSPREIS_EUR: einheitspreis_eur,
        CONF_EINHEIT: einheit,
        CONF_GRUNDGEBUEHR_EUR_MONAT: grundgebuehr_eur_monat,
        CONF_VERTEILUNG: verteilung,
        CONF_VERBRAUCH_ENTITIES_PRO_PARTEI: verbrauch_entities_pro_partei,
        CONF_AKTIV_AB: aktiv_ab,
        CONF_AKTIV_BIS: aktiv_bis,
        CONF_NOTIZ: notiz,
    }
    return ConfigSubentry(
        data=MappingProxyType(data),
        subentry_id=subentry_id,
        subentry_type=SUBENTRY_KOSTENPOSITION,
        title=bezeichnung,
        unique_id=None,
    )


def _make_entry(
    hass: HomeAssistant,
    *,
    entry_id: str = "entry-1",
    unique_id: str = "musterstrasse 1",
    title: str = "Musterstraße 1",
    subentries: list[ConfigSubentry] | None = None,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=entry_id,
        unique_id=unique_id,
        title=title,
        data={CONF_HAUS_NAME: title},
        source="user",
        subentries_data=[sub.as_dict() for sub in (subentries or [])],
    )
    entry.add_to_hass(hass)
    return entry


# ---------------------------------------------------------------------------
# Unit tests for the validation matrix helpers (no HA required)
# ---------------------------------------------------------------------------


def test_matrix_covers_all_four_zuordnung_betragsmodus_combinations() -> None:
    """Matrix must enumerate every (zuordnung, betragsmodus) combination."""
    expected_keys = {(z, b) for z in Zuordnung for b in Betragsmodus}
    assert set(_ALLOWED_VERTEILUNGEN.keys()) == expected_keys


@pytest.mark.parametrize(
    ("zuordnung", "betragsmodus", "verteilung", "expected"),
    [
        (Zuordnung.PARTEI, Betragsmodus.PAUSCHAL, Verteilung.DIREKT, True),
        (Zuordnung.PARTEI, Betragsmodus.VERBRAUCH, Verteilung.DIREKT, True),
        (Zuordnung.PARTEI, Betragsmodus.PAUSCHAL, Verteilung.GLEICH, False),
        (Zuordnung.PARTEI, Betragsmodus.VERBRAUCH, Verteilung.FLAECHE, False),
        (Zuordnung.HAUS, Betragsmodus.PAUSCHAL, Verteilung.GLEICH, True),
        (Zuordnung.HAUS, Betragsmodus.PAUSCHAL, Verteilung.DIREKT, False),
        (Zuordnung.HAUS, Betragsmodus.VERBRAUCH, Verteilung.PERSONEN, True),
        (
            Zuordnung.HAUS,
            Betragsmodus.VERBRAUCH,
            Verteilung.VERBRAUCH_SUBZAEHLER,
            True,
        ),
    ],
)
def test_is_valid_combination(
    zuordnung: Zuordnung,
    betragsmodus: Betragsmodus,
    verteilung: Verteilung,
    expected: bool,
) -> None:
    assert _is_valid_combination(zuordnung, betragsmodus, verteilung) is expected


def test_allowed_verteilungen_returns_non_empty_for_covered_combos() -> None:
    for key in _ALLOWED_VERTEILUNGEN:
        assert _allowed_verteilungen(*key)


# ---------------------------------------------------------------------------
# Main user flow tests
# ---------------------------------------------------------------------------


async def test_user_flow_happy_path(hass: HomeAssistant) -> None:
    """User submits a valid house name -> entry created."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HAUS_NAME: "Musterstraße 1"}
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Musterstraße 1"
    assert result2["data"] == {CONF_HAUS_NAME: "Musterstraße 1"}


async def test_user_flow_name_required(hass: HomeAssistant) -> None:
    """Empty name -> name_required error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HAUS_NAME: "   "}
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {CONF_HAUS_NAME: "name_required"}


async def test_user_flow_name_too_long(hass: HomeAssistant) -> None:
    """Name > 50 chars -> name_too_long error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HAUS_NAME: "x" * 60}
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {CONF_HAUS_NAME: "name_too_long"}


async def test_user_flow_duplicate_aborts(hass: HomeAssistant) -> None:
    """Second entry with the same name aborts."""
    _make_entry(hass, unique_id="musterstraße 1", title="Musterstraße 1")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HAUS_NAME: "Musterstraße 1"}
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


# ---------------------------------------------------------------------------
# Partei subentry flow
# ---------------------------------------------------------------------------


async def test_partei_create_happy_path(hass: HomeAssistant) -> None:
    entry = _make_entry(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PARTEI), context={"source": "user"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "OG (Simon)",
            CONF_FLAECHE_QM: 85.0,
            CONF_PERSONEN: 2,
            CONF_BEWOHNT_AB: "2020-01-01",
        },
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "OG (Simon)"

    # Verify the subentry was persisted
    entry_reloaded = hass.config_entries.async_get_entry(entry.entry_id)
    assert entry_reloaded is not None
    parteien = [
        s
        for s in entry_reloaded.subentries.values()
        if s.subentry_type == SUBENTRY_PARTEI
    ]
    assert len(parteien) == 1
    assert parteien[0].data[CONF_NAME] == "OG (Simon)"


async def test_partei_create_duplicate_name(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, subentries=[_partei_subentry()])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PARTEI), context={"source": "user"}
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "OG (Simon)",
            CONF_FLAECHE_QM: 85.0,
            CONF_PERSONEN: 2,
            CONF_BEWOHNT_AB: "2020-01-01",
        },
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {CONF_NAME: "name_not_unique"}


@pytest.mark.parametrize(
    ("flaeche", "error_key"),
    [
        (0.0, "invalid_flaeche"),
        (-5.0, "invalid_flaeche"),
        (1000.0, "invalid_flaeche"),
    ],
)
def test_validate_partei_invalid_flaeche(
    flaeche: float,
    error_key: str,
) -> None:
    """Server-side validator rejects out-of-range Flaeche values.

    The UI selector also enforces this boundary (client-side); this test
    exercises the server-side path that protects against API calls bypassing
    the selector.
    """
    _, errors = _validate_partei_input(
        {
            CONF_NAME: "Test",
            CONF_FLAECHE_QM: flaeche,
            CONF_PERSONEN: 2,
            CONF_BEWOHNT_AB: "2020-01-01",
        },
        existing=[],
    )
    assert errors.get(CONF_FLAECHE_QM) == error_key


@pytest.mark.parametrize("personen", [-1, 21, 99])
def test_validate_partei_invalid_personen(personen: int) -> None:
    """Server-side validator rejects out-of-range Personen values."""
    _, errors = _validate_partei_input(
        {
            CONF_NAME: "Test",
            CONF_FLAECHE_QM: 50.0,
            CONF_PERSONEN: personen,
            CONF_BEWOHNT_AB: "2020-01-01",
        },
        existing=[],
    )
    assert errors.get(CONF_PERSONEN) == "invalid_personen"


def test_validate_partei_non_numeric_personen() -> None:
    """Non-coercible input is reported as invalid_personen."""
    _, errors = _validate_partei_input(
        {
            CONF_NAME: "Test",
            CONF_FLAECHE_QM: 50.0,
            CONF_PERSONEN: "abc",
            CONF_BEWOHNT_AB: "2020-01-01",
        },
        existing=[],
    )
    assert errors.get(CONF_PERSONEN) == "invalid_personen"


def test_validate_partei_missing_bewohnt_ab() -> None:
    """Missing start date is reported via invalid_date_range on CONF_BEWOHNT_AB."""
    _, errors = _validate_partei_input(
        {
            CONF_NAME: "Test",
            CONF_FLAECHE_QM: 50.0,
            CONF_PERSONEN: 2,
            CONF_BEWOHNT_AB: None,
        },
        existing=[],
    )
    assert errors.get(CONF_BEWOHNT_AB) == "invalid_date_range"


def test_validate_partei_happy_path_normalises_output() -> None:
    """Validator returns normalised data on success."""
    data, errors = _validate_partei_input(
        {
            CONF_NAME: "  OG  ",
            CONF_FLAECHE_QM: 85.5,
            CONF_PERSONEN: 2,
            CONF_BEWOHNT_AB: date(2020, 1, 1),
            CONF_BEWOHNT_BIS: "2024-01-01",
            CONF_HINWEIS: "  Hinweis  ",
        },
        existing=[],
    )
    assert errors == {}
    assert data[CONF_NAME] == "OG"
    assert data[CONF_FLAECHE_QM] == 85.5
    assert data[CONF_BEWOHNT_AB] == "2020-01-01"
    assert data[CONF_BEWOHNT_BIS] == "2024-01-01"
    assert data[CONF_HINWEIS] == "Hinweis"


async def test_partei_invalid_date_range(hass: HomeAssistant) -> None:
    entry = _make_entry(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PARTEI), context={"source": "user"}
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test",
            CONF_FLAECHE_QM: 50.0,
            CONF_PERSONEN: 2,
            CONF_BEWOHNT_AB: "2020-06-01",
            CONF_BEWOHNT_BIS: "2020-05-01",
        },
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"].get(CONF_BEWOHNT_BIS) == "invalid_date_range"


async def test_partei_reconfigure_prefill_and_update(
    hass: HomeAssistant,
) -> None:
    partei = _partei_subentry(flaeche_qm=80.0, personen=2)
    entry = _make_entry(hass, subentries=[partei])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PARTEI),
        context={"source": "reconfigure", "subentry_id": partei.subentry_id},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "OG (Simon)",
            CONF_FLAECHE_QM: 90.0,
            CONF_PERSONEN: 3,
            CONF_BEWOHNT_AB: "2020-01-01",
            CONF_HINWEIS: "renoviert",
        },
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reconfigure_successful"

    entry_reloaded = hass.config_entries.async_get_entry(entry.entry_id)
    assert entry_reloaded is not None
    updated = entry_reloaded.subentries[partei.subentry_id]
    assert updated.data[CONF_FLAECHE_QM] == 90.0
    assert updated.data[CONF_PERSONEN] == 3
    assert updated.data[CONF_HINWEIS] == "renoviert"


async def test_partei_reconfigure_allows_same_name(hass: HomeAssistant) -> None:
    """Reconfigure should not flag the own name as duplicate."""
    partei = _partei_subentry(name="OG (Simon)")
    entry = _make_entry(hass, subentries=[partei])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PARTEI),
        context={"source": "reconfigure", "subentry_id": partei.subentry_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "OG (Simon)",
            CONF_FLAECHE_QM: 90.0,
            CONF_PERSONEN: 3,
            CONF_BEWOHNT_AB: "2020-01-01",
        },
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reconfigure_successful"


# ---------------------------------------------------------------------------
# Kostenposition subentry flow: aborts and basis validation
# ---------------------------------------------------------------------------


async def test_kostenposition_aborts_when_no_parteien(hass: HomeAssistant) -> None:
    entry = _make_entry(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_KOSTENPOSITION), context={"source": "user"}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_parteien"


async def test_kostenposition_basis_bezeichnung_required(
    hass: HomeAssistant,
) -> None:
    entry = _make_entry(hass, subentries=[_partei_subentry()])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_KOSTENPOSITION), context={"source": "user"}
    )
    assert result["step_id"] == "user"

    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_BEZEICHNUNG: "",
            CONF_KATEGORIE: Kategorie.VERSICHERUNG.value,
            CONF_ZUORDNUNG: Zuordnung.HAUS.value,
            CONF_BETRAGSMODUS: Betragsmodus.PAUSCHAL.value,
        },
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {CONF_BEZEICHNUNG: "bezeichnung_required"}


async def test_kostenposition_basis_bezeichnung_too_long(
    hass: HomeAssistant,
) -> None:
    entry = _make_entry(hass, subentries=[_partei_subentry()])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_KOSTENPOSITION), context={"source": "user"}
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_BEZEICHNUNG: "x" * 60,
            CONF_KATEGORIE: Kategorie.VERSICHERUNG.value,
            CONF_ZUORDNUNG: Zuordnung.HAUS.value,
            CONF_BETRAGSMODUS: Betragsmodus.PAUSCHAL.value,
        },
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {CONF_BEZEICHNUNG: "bezeichnung_too_long"}


# ---------------------------------------------------------------------------
# Kostenposition: HAPPY PATHS (one per valid combination)
# ---------------------------------------------------------------------------


async def test_kostenposition_haus_pauschal_flaeche(hass: HomeAssistant) -> None:
    """HAUS + PAUSCHAL + FLAECHE -> persist all fields correctly."""
    entry = _make_entry(hass, subentries=[_partei_subentry()])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_KOSTENPOSITION), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_BEZEICHNUNG: "Gebaeudeversicherung",
            CONF_KATEGORIE: Kategorie.VERSICHERUNG.value,
            CONF_ZUORDNUNG: Zuordnung.HAUS.value,
            CONF_BETRAGSMODUS: Betragsmodus.PAUSCHAL.value,
        },
    )
    assert result["step_id"] == "details"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_BETRAG_EUR: 450.0,
            CONF_PERIODIZITAET: Periodizitaet.JAEHRLICH.value,
            CONF_FAELLIGKEIT: "2026-03-15",
        },
    )
    assert result["step_id"] == "verteilung"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_VERTEILUNG: Verteilung.FLAECHE.value,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Gebaeudeversicherung"
    data = result["data"]
    assert data[CONF_ZUORDNUNG] == Zuordnung.HAUS.value
    assert data[CONF_BETRAGSMODUS] == Betragsmodus.PAUSCHAL.value
    assert data[CONF_BETRAG_EUR] == 450.0
    assert data[CONF_PERIODIZITAET] == Periodizitaet.JAEHRLICH.value
    assert data[CONF_FAELLIGKEIT] == "2026-03-15"
    assert data[CONF_VERTEILUNG] == Verteilung.FLAECHE.value
    assert data[CONF_VERBRAUCHS_ENTITY] is None
    assert data[CONF_ZUORDNUNG_PARTEI_ID] is None


async def test_kostenposition_haus_verbrauch_personen(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, subentries=[_partei_subentry()])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_KOSTENPOSITION), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_BEZEICHNUNG: "Wasser",
            CONF_KATEGORIE: Kategorie.WASSER.value,
            CONF_ZUORDNUNG: Zuordnung.HAUS.value,
            CONF_BETRAGSMODUS: Betragsmodus.VERBRAUCH.value,
        },
    )
    assert result["step_id"] == "details"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_VERBRAUCHS_ENTITY: "sensor.wasser_total",
            CONF_EINHEITSPREIS_EUR: 2.45,
            CONF_EINHEIT: Einheit.KUBIKMETER.value,
            CONF_GRUNDGEBUEHR_EUR_MONAT: 5.0,
        },
    )
    assert result["step_id"] == "verteilung"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_VERTEILUNG: Verteilung.PERSONEN.value,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_VERBRAUCHS_ENTITY] == "sensor.wasser_total"
    assert data[CONF_EINHEITSPREIS_EUR] == 2.45
    assert data[CONF_EINHEIT] == Einheit.KUBIKMETER.value
    assert data[CONF_GRUNDGEBUEHR_EUR_MONAT] == 5.0
    assert data[CONF_VERTEILUNG] == Verteilung.PERSONEN.value


async def test_kostenposition_haus_verbrauch_subzaehler(
    hass: HomeAssistant,
) -> None:
    """HAUS + VERBRAUCH + VERBRAUCH_SUBZAEHLER requires per-party entities."""
    og = _partei_subentry(subentry_id="partei-og", name="OG")
    dg = _partei_subentry(subentry_id="partei-dg", name="DG")
    entry = _make_entry(hass, subentries=[og, dg])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_KOSTENPOSITION), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_BEZEICHNUNG: "Wasser Subzaehler",
            CONF_KATEGORIE: Kategorie.WASSER.value,
            CONF_ZUORDNUNG: Zuordnung.HAUS.value,
            CONF_BETRAGSMODUS: Betragsmodus.VERBRAUCH.value,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_VERBRAUCHS_ENTITY: "sensor.wasser_total",
            CONF_EINHEITSPREIS_EUR: 2.45,
            CONF_EINHEIT: Einheit.KUBIKMETER.value,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_VERTEILUNG: Verteilung.VERBRAUCH_SUBZAEHLER.value,
        },
    )
    assert result["step_id"] == "subzaehler"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            f"entity_{og.subentry_id}": "sensor.wasser_og",
            f"entity_{dg.subentry_id}": "sensor.wasser_dg",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    mapping = result["data"][CONF_VERBRAUCH_ENTITIES_PRO_PARTEI]
    assert mapping == {
        og.subentry_id: "sensor.wasser_og",
        dg.subentry_id: "sensor.wasser_dg",
    }


async def test_kostenposition_partei_pauschal_direkt(hass: HomeAssistant) -> None:
    og = _partei_subentry(subentry_id="partei-og", name="OG")
    entry = _make_entry(hass, subentries=[og])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_KOSTENPOSITION), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_BEZEICHNUNG: "Grundgebuehr Strom OG",
            CONF_KATEGORIE: Kategorie.STROM.value,
            CONF_ZUORDNUNG: Zuordnung.PARTEI.value,
            CONF_BETRAGSMODUS: Betragsmodus.PAUSCHAL.value,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_ZUORDNUNG_PARTEI_ID: og.subentry_id,
            CONF_BETRAG_EUR: 8.90,
            CONF_PERIODIZITAET: Periodizitaet.MONATLICH.value,
            CONF_FAELLIGKEIT: "2026-01-01",
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_VERTEILUNG: Verteilung.DIREKT.value},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ZUORDNUNG_PARTEI_ID] == og.subentry_id
    assert result["data"][CONF_VERTEILUNG] == Verteilung.DIREKT.value


async def test_kostenposition_partei_verbrauch_direkt(hass: HomeAssistant) -> None:
    og = _partei_subentry(subentry_id="partei-og", name="OG")
    entry = _make_entry(hass, subentries=[og])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_KOSTENPOSITION), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_BEZEICHNUNG: "Strom OG",
            CONF_KATEGORIE: Kategorie.STROM.value,
            CONF_ZUORDNUNG: Zuordnung.PARTEI.value,
            CONF_BETRAGSMODUS: Betragsmodus.VERBRAUCH.value,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_ZUORDNUNG_PARTEI_ID: og.subentry_id,
            CONF_VERBRAUCHS_ENTITY: "sensor.strom_og",
            CONF_EINHEITSPREIS_EUR: 0.35,
            CONF_EINHEIT: Einheit.KWH.value,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_VERTEILUNG: Verteilung.DIREKT.value},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ZUORDNUNG_PARTEI_ID] == og.subentry_id
    assert result["data"][CONF_VERBRAUCHS_ENTITY] == "sensor.strom_og"


# ---------------------------------------------------------------------------
# Kostenposition: DETAIL-step validation
# ---------------------------------------------------------------------------


def test_validate_details_partei_required() -> None:
    """Server-side validator rejects missing partei_id for PARTEI zuordnung."""
    og = _partei_subentry(subentry_id="partei-og", name="OG")
    errors = _validate_details_input(
        {
            CONF_ZUORDNUNG_PARTEI_ID: "",
            CONF_BETRAG_EUR: 10.0,
            CONF_PERIODIZITAET: Periodizitaet.MONATLICH.value,
            CONF_FAELLIGKEIT: "2026-01-01",
        },
        zuordnung=Zuordnung.PARTEI,
        betragsmodus=Betragsmodus.PAUSCHAL,
        parteien=[og],
    )
    assert errors.get(CONF_ZUORDNUNG_PARTEI_ID) == "partei_required"


def test_validate_details_partei_unknown_id_rejected() -> None:
    """Unknown partei_id is rejected, even if non-empty."""
    og = _partei_subentry(subentry_id="partei-og", name="OG")
    errors = _validate_details_input(
        {
            CONF_ZUORDNUNG_PARTEI_ID: "ghost",
            CONF_BETRAG_EUR: 10.0,
            CONF_PERIODIZITAET: Periodizitaet.MONATLICH.value,
            CONF_FAELLIGKEIT: "2026-01-01",
        },
        zuordnung=Zuordnung.PARTEI,
        betragsmodus=Betragsmodus.PAUSCHAL,
        parteien=[og],
    )
    assert errors.get(CONF_ZUORDNUNG_PARTEI_ID) == "partei_required"


def test_validate_details_pauschal_missing_fields() -> None:
    errors = _validate_details_input(
        {},
        zuordnung=Zuordnung.HAUS,
        betragsmodus=Betragsmodus.PAUSCHAL,
        parteien=[],
    )
    assert errors.get(CONF_BETRAG_EUR) == "betrag_required"
    assert errors.get(CONF_PERIODIZITAET) == "periodizitaet_required"
    assert errors.get(CONF_FAELLIGKEIT) == "faelligkeit_required"


def test_validate_details_pauschal_betrag_negative() -> None:
    errors = _validate_details_input(
        {
            CONF_BETRAG_EUR: -5.0,
            CONF_PERIODIZITAET: Periodizitaet.JAEHRLICH.value,
            CONF_FAELLIGKEIT: "2026-01-01",
        },
        zuordnung=Zuordnung.HAUS,
        betragsmodus=Betragsmodus.PAUSCHAL,
        parteien=[],
    )
    assert errors.get(CONF_BETRAG_EUR) == "betrag_required"


def test_validate_details_verbrauch_missing_fields() -> None:
    errors = _validate_details_input(
        {
            CONF_VERBRAUCHS_ENTITY: "",
            CONF_EINHEITSPREIS_EUR: -1.0,
            CONF_EINHEIT: "",
        },
        zuordnung=Zuordnung.HAUS,
        betragsmodus=Betragsmodus.VERBRAUCH,
        parteien=[],
    )
    assert errors.get(CONF_VERBRAUCHS_ENTITY) == "verbrauchs_entity_required"
    assert errors.get(CONF_EINHEITSPREIS_EUR) == "einheitspreis_required"
    assert errors.get(CONF_EINHEIT) == "einheit_required"


# ---------------------------------------------------------------------------
# Kostenposition: VERTEILUNG-step validation
# ---------------------------------------------------------------------------


async def test_kostenposition_verteilung_schema_rejects_invalid_option(
    hass: HomeAssistant,
) -> None:
    """The distribution selector only exposes valid options (client-side).

    When user input attempts to submit a distribution outside the allowed
    set for the chosen (zuordnung, betragsmodus), the voluptuous schema
    raises ``InvalidData`` before the server-side validator runs. This
    protects the user from committing invalid combinations.
    """
    og = _partei_subentry(subentry_id="partei-og", name="OG")
    entry = _make_entry(hass, subentries=[og])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_KOSTENPOSITION), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_BEZEICHNUNG: "x",
            CONF_KATEGORIE: Kategorie.STROM.value,
            CONF_ZUORDNUNG: Zuordnung.PARTEI.value,
            CONF_BETRAGSMODUS: Betragsmodus.PAUSCHAL.value,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_ZUORDNUNG_PARTEI_ID: og.subentry_id,
            CONF_BETRAG_EUR: 10.0,
            CONF_PERIODIZITAET: Periodizitaet.MONATLICH.value,
            CONF_FAELLIGKEIT: "2026-01-01",
        },
    )
    # The schema on step "verteilung" only accepts DIREKT for this combo.
    with pytest.raises(InvalidData):
        await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {CONF_VERTEILUNG: Verteilung.GLEICH.value},
        )


async def test_kostenposition_verteilung_invalid_date_range(
    hass: HomeAssistant,
) -> None:
    entry = _make_entry(hass, subentries=[_partei_subentry()])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_KOSTENPOSITION), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_BEZEICHNUNG: "x",
            CONF_KATEGORIE: Kategorie.VERSICHERUNG.value,
            CONF_ZUORDNUNG: Zuordnung.HAUS.value,
            CONF_BETRAGSMODUS: Betragsmodus.PAUSCHAL.value,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_BETRAG_EUR: 100.0,
            CONF_PERIODIZITAET: Periodizitaet.JAEHRLICH.value,
            CONF_FAELLIGKEIT: "2026-01-01",
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_VERTEILUNG: Verteilung.FLAECHE.value,
            CONF_AKTIV_AB: "2026-06-01",
            CONF_AKTIV_BIS: "2026-05-01",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"].get(CONF_AKTIV_BIS) == "invalid_date_range"


# ---------------------------------------------------------------------------
# Kostenposition: RECONFIGURE
# ---------------------------------------------------------------------------


async def test_kostenposition_reconfigure_updates_amount(
    hass: HomeAssistant,
) -> None:
    """Reconfigure an existing Kostenposition -> amount updated."""
    partei = _partei_subentry()
    kp = _kp_subentry(
        subentry_id="kp-1",
        bezeichnung="Gebaeudeversicherung",
        betrag_eur=450.0,
        verteilung=Verteilung.FLAECHE.value,
    )
    entry = _make_entry(hass, subentries=[partei, kp])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_KOSTENPOSITION),
        context={"source": "reconfigure", "subentry_id": kp.subentry_id},
    )
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_BEZEICHNUNG: "Gebaeudeversicherung",
            CONF_KATEGORIE: Kategorie.VERSICHERUNG.value,
            CONF_ZUORDNUNG: Zuordnung.HAUS.value,
            CONF_BETRAGSMODUS: Betragsmodus.PAUSCHAL.value,
        },
    )
    assert result["step_id"] == "details"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_BETRAG_EUR: 500.0,
            CONF_PERIODIZITAET: Periodizitaet.JAEHRLICH.value,
            CONF_FAELLIGKEIT: "2026-03-15",
        },
    )
    assert result["step_id"] == "verteilung"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_VERTEILUNG: Verteilung.FLAECHE.value},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    entry_reloaded = hass.config_entries.async_get_entry(entry.entry_id)
    assert entry_reloaded is not None
    updated = entry_reloaded.subentries[kp.subentry_id]
    assert updated.data[CONF_BETRAG_EUR] == 500.0


async def test_kostenposition_reconfigure_aborts_when_no_parteien(
    hass: HomeAssistant,
) -> None:
    """Reconfigure aborts when all parteien are gone (edge case)."""
    kp = _kp_subentry(subentry_id="kp-1")
    entry = _make_entry(hass, subentries=[kp])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_KOSTENPOSITION),
        context={"source": "reconfigure", "subentry_id": kp.subentry_id},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_parteien"


# ---------------------------------------------------------------------------
# Kostenposition flow: ABSCHLAG (phase 5)
# ---------------------------------------------------------------------------


async def test_kostenposition_haus_abschlag_personen_without_verbrauch(
    hass: HomeAssistant,
) -> None:
    """HAUS + ABSCHLAG without consumption sensor persists only prepayment."""
    entry = _make_entry(hass, subentries=[_partei_subentry()])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_KOSTENPOSITION), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_BEZEICHNUNG: "Wasser",
            CONF_KATEGORIE: Kategorie.WASSER.value,
            CONF_ZUORDNUNG: Zuordnung.HAUS.value,
            CONF_BETRAGSMODUS: Betragsmodus.ABSCHLAG.value,
        },
    )
    assert result["step_id"] == "details"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_MONATLICHER_ABSCHLAG_EUR: 50.0,
            CONF_ABRECHNUNGSZEITRAUM_START: "2026-01-01",
            CONF_ABRECHNUNGSZEITRAUM_DAUER_MONATE: 12,
        },
    )
    assert result["step_id"] == "verteilung"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_VERTEILUNG: Verteilung.PERSONEN.value},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_BETRAGSMODUS] == Betragsmodus.ABSCHLAG.value
    assert data[CONF_MONATLICHER_ABSCHLAG_EUR] == 50.0
    assert data[CONF_ABRECHNUNGSZEITRAUM_START] == "2026-01-01"
    assert data[CONF_ABRECHNUNGSZEITRAUM_DAUER_MONATE] == 12
    # Consumption fields absent -> stay None.
    assert data[CONF_VERBRAUCHS_ENTITY] is None
    assert data[CONF_EINHEITSPREIS_EUR] is None
    assert data[CONF_EINHEIT] is None
    assert data[CONF_GRUNDGEBUEHR_EUR_MONAT] is None


async def test_kostenposition_haus_abschlag_with_verbrauch(
    hass: HomeAssistant,
) -> None:
    """ABSCHLAG with a consumption sensor also persists price + unit + fee."""
    entry = _make_entry(hass, subentries=[_partei_subentry()])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_KOSTENPOSITION), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_BEZEICHNUNG: "Wasser",
            CONF_KATEGORIE: Kategorie.WASSER.value,
            CONF_ZUORDNUNG: Zuordnung.HAUS.value,
            CONF_BETRAGSMODUS: Betragsmodus.ABSCHLAG.value,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_MONATLICHER_ABSCHLAG_EUR: 50.0,
            CONF_ABRECHNUNGSZEITRAUM_START: "2026-01-01",
            CONF_ABRECHNUNGSZEITRAUM_DAUER_MONATE: 12,
            CONF_VERBRAUCHS_ENTITY: "sensor.wasserzaehler",
            CONF_EINHEITSPREIS_EUR: 3.5,
            CONF_EINHEIT: Einheit.KUBIKMETER.value,
            CONF_GRUNDGEBUEHR_EUR_MONAT: 5.0,
        },
    )
    assert result["step_id"] == "verteilung"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_VERTEILUNG: Verteilung.PERSONEN.value},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_MONATLICHER_ABSCHLAG_EUR] == 50.0
    assert data[CONF_VERBRAUCHS_ENTITY] == "sensor.wasserzaehler"
    assert data[CONF_EINHEITSPREIS_EUR] == 3.5
    assert data[CONF_EINHEIT] == Einheit.KUBIKMETER.value
    assert data[CONF_GRUNDGEBUEHR_EUR_MONAT] == 5.0


def test_validate_details_abschlag_missing_fields() -> None:
    """Missing monthly + period start surface as errors."""
    errors = _validate_details_input(
        {
            # both required fields missing
            CONF_MONATLICHER_ABSCHLAG_EUR: None,
            CONF_ABRECHNUNGSZEITRAUM_START: None,
            CONF_ABRECHNUNGSZEITRAUM_DAUER_MONATE: 12,
        },
        zuordnung=Zuordnung.HAUS,
        betragsmodus=Betragsmodus.ABSCHLAG,
        parteien=[],
    )
    assert errors[CONF_MONATLICHER_ABSCHLAG_EUR] == "abschlag_required"
    assert errors[CONF_ABRECHNUNGSZEITRAUM_START] == "zeitraum_start_required"


def test_validate_details_abschlag_negative_amount() -> None:
    """Negative monthly amount is rejected."""
    errors = _validate_details_input(
        {
            CONF_MONATLICHER_ABSCHLAG_EUR: -5.0,
            CONF_ABRECHNUNGSZEITRAUM_START: "2026-01-01",
            CONF_ABRECHNUNGSZEITRAUM_DAUER_MONATE: 12,
        },
        zuordnung=Zuordnung.HAUS,
        betragsmodus=Betragsmodus.ABSCHLAG,
        parteien=[],
    )
    assert errors[CONF_MONATLICHER_ABSCHLAG_EUR] == "abschlag_required"


def test_validate_details_abschlag_dauer_out_of_range() -> None:
    """Duration outside [1, 36] is rejected."""
    for bad in (0, -3, 37):
        errors = _validate_details_input(
            {
                CONF_MONATLICHER_ABSCHLAG_EUR: 50.0,
                CONF_ABRECHNUNGSZEITRAUM_START: "2026-01-01",
                CONF_ABRECHNUNGSZEITRAUM_DAUER_MONATE: bad,
            },
            zuordnung=Zuordnung.HAUS,
            betragsmodus=Betragsmodus.ABSCHLAG,
            parteien=[],
        )
        assert errors[CONF_ABRECHNUNGSZEITRAUM_DAUER_MONATE] == "dauer_invalid"


def test_validate_details_abschlag_verbrauch_requires_price_and_unit() -> None:
    """If a sensor is set, price and unit become required."""
    errors = _validate_details_input(
        {
            CONF_MONATLICHER_ABSCHLAG_EUR: 50.0,
            CONF_ABRECHNUNGSZEITRAUM_START: "2026-01-01",
            CONF_ABRECHNUNGSZEITRAUM_DAUER_MONATE: 12,
            CONF_VERBRAUCHS_ENTITY: "sensor.wasser",
            # price + unit missing
        },
        zuordnung=Zuordnung.HAUS,
        betragsmodus=Betragsmodus.ABSCHLAG,
        parteien=[],
    )
    assert errors[CONF_EINHEITSPREIS_EUR] == "einheitspreis_required"
    assert errors[CONF_EINHEIT] == "einheit_required"


def test_validate_details_abschlag_without_verbrauch_price_unit_optional() -> None:
    """Without a sensor, price and unit stay optional."""
    errors = _validate_details_input(
        {
            CONF_MONATLICHER_ABSCHLAG_EUR: 50.0,
            CONF_ABRECHNUNGSZEITRAUM_START: "2026-01-01",
            CONF_ABRECHNUNGSZEITRAUM_DAUER_MONATE: 12,
        },
        zuordnung=Zuordnung.HAUS,
        betragsmodus=Betragsmodus.ABSCHLAG,
        parteien=[],
    )
    assert errors == {}
