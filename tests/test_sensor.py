"""Tests for custom_components.hauskosten.sensor.

Authoritative oracles:
* docs/ARCHITECTURE.md -- "Sensor-Platform" section: dynamic per-party,
  per-category and house-wide sensor generation driven by coordinator data.
* docs/STANDARDS.md    -- "Entity-Design": ``_attr_has_entity_name = True``,
  translation keys, unique_id schema, device / state classes.
* .claude/agents/sensor-dev.md -- unique-id pattern and entity catalog.
* AGENTS.md hard constraints:
    #2 the integration only exposes sensors,
    #7 all user-facing strings go through translations.

The tests drive the sensor platform via ``hass.config_entries.async_setup``
so we implicitly verify that platform forwarding, dynamic entity creation
and the update listener are wired correctly.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import CURRENCY_EURO
from homeassistant.helpers import entity_registry as er

from custom_components.hauskosten.const import (
    DOMAIN,
    SUBENTRY_KOSTENPOSITION,
    SUBENTRY_PARTEI,
)
from custom_components.hauskosten.coordinator import HauskostenCoordinator
from custom_components.hauskosten.models import Kategorie
from custom_components.hauskosten.sensor import (
    HausAbschlagGezahltSensor,
    HausAbschlagIstSensor,
    HausAbschlagSaldoSensor,
    HausKategorieSensor,
    ParteiAbschlagGezahltSensor,
    ParteiAbschlagIstSensor,
    ParteiAbschlagSaldoSensor,
    ParteiJahrAktuellSensor,
    ParteiJahrBudgetSensor,
    ParteiKategorieSensor,
    ParteiMonatSensor,
    ParteiNaechsteFaelligkeitSensor,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _partei_subentry(
    *,
    subentry_id: str = "partei-og",
    name: str = "OG",
    flaeche_qm: float = 85.0,
    personen: int = 2,
    bewohnt_ab: date = date(2020, 1, 1),
    bewohnt_bis: date | None = None,
) -> ConfigSubentry:
    """Build a ConfigSubentry of type 'partei' for MockConfigEntry."""
    data: dict[str, Any] = {
        "name": name,
        "flaeche_qm": flaeche_qm,
        "personen": personen,
        "bewohnt_ab": bewohnt_ab.isoformat(),
        "bewohnt_bis": bewohnt_bis.isoformat() if bewohnt_bis else None,
        "hinweis": None,
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
    subentry_id: str,
    bezeichnung: str,
    kategorie: str = "versicherung",
    zuordnung: str = "haus",
    zuordnung_partei_id: str | None = None,
    betragsmodus: str = "pauschal",
    betrag_eur: float | None = 450.0,
    periodizitaet: str | None = "jaehrlich",
    faelligkeit: date | None = date(2026, 3, 15),
    verbrauchs_entity: str | None = None,
    einheitspreis_eur: float | None = None,
    einheit: str | None = None,
    grundgebuehr_eur_monat: float | None = None,
    monatlicher_abschlag_eur: float | None = None,
    abrechnungszeitraum_start: date | None = None,
    abrechnungszeitraum_dauer_monate: int | None = None,
    verteilung: str = "flaeche",
) -> ConfigSubentry:
    """Build a ConfigSubentry of type 'kostenposition'."""
    data: dict[str, Any] = {
        "bezeichnung": bezeichnung,
        "kategorie": kategorie,
        "zuordnung": zuordnung,
        "zuordnung_partei_id": zuordnung_partei_id,
        "betragsmodus": betragsmodus,
        "betrag_eur": betrag_eur,
        "periodizitaet": periodizitaet,
        "faelligkeit": faelligkeit.isoformat() if faelligkeit else None,
        "verbrauchs_entity": verbrauchs_entity,
        "einheitspreis_eur": einheitspreis_eur,
        "einheit": einheit,
        "grundgebuehr_eur_monat": grundgebuehr_eur_monat,
        "monatlicher_abschlag_eur": monatlicher_abschlag_eur,
        "abrechnungszeitraum_start": (
            abrechnungszeitraum_start.isoformat() if abrechnungszeitraum_start else None
        ),
        "abrechnungszeitraum_dauer_monate": abrechnungszeitraum_dauer_monate,
        "verteilung": verteilung,
        "verbrauch_entities_pro_partei": None,
        "aktiv_ab": None,
        "aktiv_bis": None,
        "notiz": None,
    }
    return ConfigSubentry(
        data=MappingProxyType(data),
        subentry_id=subentry_id,
        subentry_type=SUBENTRY_KOSTENPOSITION,
        title=bezeichnung,
        unique_id=None,
    )


def _make_entry(
    *subentries: ConfigSubentry,
    entry_id: str = "entry-sensor-test",
    title: str = "Haus",
) -> MockConfigEntry:
    """Build a MockConfigEntry with the given subentries."""
    subentries_data = [
        {
            "data": dict(s.data),
            "subentry_id": s.subentry_id,
            "subentry_type": s.subentry_type,
            "title": s.title,
            "unique_id": s.unique_id,
        }
        for s in subentries
    ]
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={"haus_name": title},
        entry_id=entry_id,
        subentries_data=subentries_data,
    )


async def _setup_entry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
) -> None:
    """Add entry to hass, run setup and wait for all tasks."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _sensor_unique_ids(hass: HomeAssistant, entry_id: str) -> set[str]:
    """Return the set of unique_ids of all sensor entities for this entry."""
    registry = er.async_get(hass)
    return {
        e.unique_id
        for e in registry.entities.values()
        if e.config_entry_id == entry_id and e.domain == "sensor"
    }


# ---------------------------------------------------------------------------
# Empty entry: only house-wide sensors
# ---------------------------------------------------------------------------


class TestEmptySetup:
    """With no parteien / kostenpositionen, only house sensors are created."""

    async def test_empty_entry_creates_only_haus_sensors(
        self,
        hass: HomeAssistant,
    ) -> None:
        entry = _make_entry()
        await _setup_entry(hass, entry)

        unique_ids = _sensor_unique_ids(hass, entry.entry_id)
        # Expect: haus_jahr_gesamt, haus_jahr_budget, naechste_faelligkeit
        # No per-category sensors (haus.pro_kategorie_jahr_eur is empty).
        assert f"{entry.entry_id}_haus_jahr_gesamt" in unique_ids
        assert f"{entry.entry_id}_haus_jahr_budget" in unique_ids
        assert f"{entry.entry_id}_haus_naechste_faelligkeit" in unique_ids
        # Nothing party-scoped.
        for uid in unique_ids:
            assert "_partei_" not in uid

    async def test_empty_entry_haus_totals_are_zero(
        self,
        hass: HomeAssistant,
    ) -> None:
        entry = _make_entry()
        await _setup_entry(hass, entry)

        state = hass.states.get("sensor.haus_haus_gesamtkosten_2026")
        # The entity_id slug depends on HA translation; fall back to registry.
        registry = er.async_get(hass)
        gesamt_entry = next(
            e
            for e in registry.entities.values()
            if e.unique_id == f"{entry.entry_id}_haus_jahr_gesamt"
        )
        state = hass.states.get(gesamt_entry.entity_id)
        assert state is not None
        assert float(state.state) == 0.0


# ---------------------------------------------------------------------------
# Single partei + single pauschal position
# ---------------------------------------------------------------------------


class TestSinglePartei:
    """One party + one pauschal position yields the full partei sensor set."""

    async def test_expected_partei_sensors_exist(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-vers",
            bezeichnung="Versicherung",
            kategorie="versicherung",
            betrag_eur=450.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        await _setup_entry(hass, entry)

        unique_ids = _sensor_unique_ids(hass, entry.entry_id)

        # Core per-party sensors.
        assert f"{entry.entry_id}_partei_og_monat_aktuell" in unique_ids
        assert f"{entry.entry_id}_partei_og_jahr_aktuell" in unique_ids
        assert f"{entry.entry_id}_partei_og_jahr_budget" in unique_ids
        assert f"{entry.entry_id}_partei_og_naechste_faelligkeit" in unique_ids

        # One category sensor for versicherung.
        assert f"{entry.entry_id}_partei_og_kategorie_versicherung_jahr" in unique_ids

        # House-level sensors.
        assert f"{entry.entry_id}_haus_jahr_gesamt" in unique_ids
        assert f"{entry.entry_id}_haus_jahr_budget" in unique_ids
        assert f"{entry.entry_id}_haus_kategorie_versicherung_jahr" in unique_ids
        assert f"{entry.entry_id}_haus_naechste_faelligkeit" in unique_ids

    async def test_partei_jahr_budget_state_matches_coordinator(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-vers",
            bezeichnung="Versicherung",
            betrag_eur=450.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        await _setup_entry(hass, entry)

        registry = er.async_get(hass)
        entry_entity = next(
            e
            for e in registry.entities.values()
            if e.unique_id == f"{entry.entry_id}_partei_og_jahr_budget"
        )
        state = hass.states.get(entry_entity.entity_id)
        assert state is not None
        assert float(state.state) == pytest.approx(450.0)

    async def test_partei_monat_sensor_has_euro_unit(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-vers",
            bezeichnung="Versicherung",
            betrag_eur=1200.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        await _setup_entry(hass, entry)

        registry = er.async_get(hass)
        entity = next(
            e
            for e in registry.entities.values()
            if e.unique_id == f"{entry.entry_id}_partei_og_monat_aktuell"
        )
        state = hass.states.get(entity.entity_id)
        assert state is not None
        assert float(state.state) == pytest.approx(100.0)
        assert state.attributes["unit_of_measurement"] == CURRENCY_EURO
        assert state.attributes["device_class"] == SensorDeviceClass.MONETARY
        assert state.attributes["state_class"] == SensorStateClass.TOTAL


# ---------------------------------------------------------------------------
# Two parteien + multiple categories
# ---------------------------------------------------------------------------


class TestTwoParteienMultipleCategories:
    """Per-category sensors appear per party AND for the house totals."""

    async def test_category_sensors_per_party_and_house(
        self,
        hass: HomeAssistant,
    ) -> None:
        og = _partei_subentry(subentry_id="og", name="OG", flaeche_qm=85.0)
        dg = _partei_subentry(
            subentry_id="dg",
            name="DG",
            flaeche_qm=65.0,
            bewohnt_ab=date(2019, 1, 1),
        )
        kp_vers = _kp_subentry(
            subentry_id="kp-vers",
            bezeichnung="Versicherung",
            kategorie="versicherung",
            betrag_eur=450.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
        )
        kp_muell = _kp_subentry(
            subentry_id="kp-muell",
            bezeichnung="Muell",
            kategorie="muell",
            betrag_eur=240.0,
            periodizitaet="jaehrlich",
            verteilung="personen",
        )
        entry = _make_entry(og, dg, kp_vers, kp_muell)
        await _setup_entry(hass, entry)

        unique_ids = _sensor_unique_ids(hass, entry.entry_id)

        # Both categories for both parties.
        assert f"{entry.entry_id}_partei_og_kategorie_versicherung_jahr" in unique_ids
        assert f"{entry.entry_id}_partei_dg_kategorie_versicherung_jahr" in unique_ids
        assert f"{entry.entry_id}_partei_og_kategorie_muell_jahr" in unique_ids
        assert f"{entry.entry_id}_partei_dg_kategorie_muell_jahr" in unique_ids

        # Both categories on haus level.
        assert f"{entry.entry_id}_haus_kategorie_versicherung_jahr" in unique_ids
        assert f"{entry.entry_id}_haus_kategorie_muell_jahr" in unique_ids


# ---------------------------------------------------------------------------
# Coordinator refresh propagates to sensor state
# ---------------------------------------------------------------------------


class TestCoordinatorRefreshPropagates:
    """Sensor states reflect the latest coordinator.data after a refresh."""

    async def test_state_updates_on_coordinator_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp_data = {
            "bezeichnung": "Wasser",
            "kategorie": "wasser",
            "zuordnung": "haus",
            "zuordnung_partei_id": None,
            "betragsmodus": "verbrauch",
            "betrag_eur": None,
            "periodizitaet": None,
            "faelligkeit": None,
            "verbrauchs_entity": "sensor.wasser",
            "einheitspreis_eur": 3.0,
            "einheit": "m3",
            "grundgebuehr_eur_monat": None,
            "verteilung": "flaeche",
            "verbrauch_entities_pro_partei": None,
            "aktiv_ab": None,
            "aktiv_bis": None,
            "notiz": None,
        }
        kp = ConfigSubentry(
            data=MappingProxyType(kp_data),
            subentry_id="kp-w",
            subentry_type=SUBENTRY_KOSTENPOSITION,
            title="Wasser",
            unique_id=None,
        )
        hass.states.async_set("sensor.wasser", "40")
        entry = _make_entry(partei, kp)
        await _setup_entry(hass, entry)

        registry = er.async_get(hass)
        budget_entity = next(
            e
            for e in registry.entities.values()
            if e.unique_id == f"{entry.entry_id}_partei_og_jahr_budget"
        )

        state = hass.states.get(budget_entity.entity_id)
        assert state is not None
        assert float(state.state) == pytest.approx(120.0)

        # Refresh with a higher verbrauch value.
        hass.states.async_set("sensor.wasser", "60")
        await hass.async_block_till_done()
        await hass.async_block_till_done()

        state = hass.states.get(budget_entity.entity_id)
        assert state is not None
        assert float(state.state) == pytest.approx(180.0)


# ---------------------------------------------------------------------------
# Dynamic add: a new partei after setup spawns new sensors
# ---------------------------------------------------------------------------


class TestDynamicSubentryAdd:
    """Adding a partei after setup must produce new sensors without reload."""

    async def test_new_partei_spawns_new_sensors(
        self,
        hass: HomeAssistant,
    ) -> None:
        og = _partei_subentry(subentry_id="og", name="OG", flaeche_qm=85.0)
        kp = _kp_subentry(
            subentry_id="kp-vers",
            bezeichnung="Versicherung",
            kategorie="versicherung",
            betrag_eur=450.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
        )
        entry = _make_entry(og, kp)
        await _setup_entry(hass, entry)

        initial_ids = _sensor_unique_ids(hass, entry.entry_id)
        assert f"{entry.entry_id}_partei_og_jahr_budget" in initial_ids
        assert f"{entry.entry_id}_partei_dg_jahr_budget" not in initial_ids

        # Simulate adding a DG partei via config-subentry API.
        dg_subentry = ConfigSubentry(
            data=MappingProxyType(
                {
                    "name": "DG",
                    "flaeche_qm": 65.0,
                    "personen": 1,
                    "bewohnt_ab": date(2019, 1, 1).isoformat(),
                    "bewohnt_bis": None,
                    "hinweis": None,
                }
            ),
            subentry_id="dg",
            subentry_type=SUBENTRY_PARTEI,
            title="DG",
            unique_id=None,
        )
        hass.config_entries.async_add_subentry(entry, dg_subentry)
        await hass.async_block_till_done()

        coord: HauskostenCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        await coord.async_request_refresh()
        await hass.async_block_till_done()

        after_ids = _sensor_unique_ids(hass, entry.entry_id)
        assert f"{entry.entry_id}_partei_dg_jahr_budget" in after_ids
        assert f"{entry.entry_id}_partei_dg_kategorie_versicherung_jahr" in after_ids


# ---------------------------------------------------------------------------
# Entity metadata: device_info, translation_key, has_entity_name
# ---------------------------------------------------------------------------


class TestEntityMetadata:
    """Entities must be grouped under a device and use translation keys."""

    async def test_device_info_groups_entities_under_entry(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        entry = _make_entry(partei, title="Musterstrasse 1")
        await _setup_entry(hass, entry)

        registry = er.async_get(hass)
        entities = [
            e for e in registry.entities.values() if e.config_entry_id == entry.entry_id
        ]
        assert len(entities) > 0

        # All entities share the same device_id.
        device_ids = {e.device_id for e in entities}
        assert len(device_ids) == 1
        assert next(iter(device_ids)) is not None

    async def test_has_entity_name_and_translation_key(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        entry = _make_entry(partei)
        await _setup_entry(hass, entry)

        registry = er.async_get(hass)
        entity_entry = next(
            e
            for e in registry.entities.values()
            if e.unique_id == f"{entry.entry_id}_partei_og_jahr_budget"
        )
        # Registry exposes the translation key from the entity class.
        assert entity_entry.translation_key == "partei_jahr_budget"
        assert entity_entry.has_entity_name is True
        # The resolved name uses the translation + placeholders.
        assert entity_entry.original_name is not None
        assert "OG" in entity_entry.original_name


# ---------------------------------------------------------------------------
# Stability: unique_ids are driven by IDs not names
# ---------------------------------------------------------------------------


class TestUniqueIdStability:
    """Unique-IDs must be stable across party renames (not name-based)."""

    async def test_unique_id_is_based_on_subentry_id(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="stable-id", name="Original")
        entry = _make_entry(partei)
        await _setup_entry(hass, entry)

        unique_ids = _sensor_unique_ids(hass, entry.entry_id)
        for uid in unique_ids:
            # No entity has "original" (the name) in the unique id.
            assert "original" not in uid.lower()
        # All per-party unique ids embed the subentry id.
        party_ids = [uid for uid in unique_ids if "_partei_" in uid]
        for uid in party_ids:
            assert "stable-id" in uid


# ---------------------------------------------------------------------------
# Unavailable behaviour: partei disappears from coordinator data
# ---------------------------------------------------------------------------


class TestAvailability:
    """Sensors become unavailable when their coordinator data is missing."""

    async def test_sensor_unavailable_when_party_vanishes(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        entry = _make_entry(partei)
        await _setup_entry(hass, entry)

        coord: HauskostenCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        # Surgically remove the party from coordinator.data to simulate a
        # stale entity lingering after the party was deleted.
        assert coord.data is not None
        coord.data["parteien"].pop("og", None)
        coord.async_update_listeners()
        await hass.async_block_till_done()

        registry = er.async_get(hass)
        entry_entity = next(
            e
            for e in registry.entities.values()
            if e.unique_id == f"{entry.entry_id}_partei_og_jahr_budget"
        )
        state = hass.states.get(entry_entity.entity_id)
        assert state is not None
        assert state.state == "unavailable"


# ---------------------------------------------------------------------------
# Naechste Faelligkeit behaviour
# ---------------------------------------------------------------------------


class TestFaelligkeitSensors:
    """Fälligkeits-sensors must expose device_class DATE and ISO dates."""

    async def test_naechste_faelligkeit_returns_iso_date(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-vers",
            bezeichnung="Versicherung",
            betrag_eur=450.0,
            periodizitaet="jaehrlich",
            faelligkeit=date(2026, 12, 31),
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        await _setup_entry(hass, entry)

        registry = er.async_get(hass)
        ent = next(
            e
            for e in registry.entities.values()
            if e.unique_id == f"{entry.entry_id}_partei_og_naechste_faelligkeit"
        )
        state = hass.states.get(ent.entity_id)
        assert state is not None
        assert state.attributes["device_class"] == SensorDeviceClass.DATE
        # HA renders date sensors as ISO-format strings.
        assert state.state == "2026-12-31"

    async def test_naechste_faelligkeit_unknown_when_no_positions(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        entry = _make_entry(partei)
        await _setup_entry(hass, entry)

        registry = er.async_get(hass)
        ent = next(
            e
            for e in registry.entities.values()
            if e.unique_id == f"{entry.entry_id}_partei_og_naechste_faelligkeit"
        )
        state = hass.states.get(ent.entity_id)
        assert state is not None
        # No due date means the sensor reports "unknown".
        assert state.state == "unknown"


# ---------------------------------------------------------------------------
# Category zero-value: no sensor for a category with 0 EUR
# ---------------------------------------------------------------------------


class TestCategoryZeroValue:
    """Categories without cost must not create noise sensors."""

    async def test_no_sensor_for_absent_category(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-vers",
            bezeichnung="Versicherung",
            kategorie="versicherung",
            betrag_eur=450.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        await _setup_entry(hass, entry)

        unique_ids = _sensor_unique_ids(hass, entry.entry_id)
        # versicherung yes, strom no.
        assert f"{entry.entry_id}_partei_og_kategorie_versicherung_jahr" in unique_ids
        assert f"{entry.entry_id}_partei_og_kategorie_strom_jahr" not in unique_ids


# ---------------------------------------------------------------------------
# Attributes expose positionen for drill-down
# ---------------------------------------------------------------------------


class TestAttributes:
    """The jahr_aktuell sensor exposes the positionen list for drill-down."""

    async def test_jahr_aktuell_lists_positions(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-vers",
            bezeichnung="Versicherung",
            kategorie="versicherung",
            betrag_eur=450.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        await _setup_entry(hass, entry)

        registry = er.async_get(hass)
        ent = next(
            e
            for e in registry.entities.values()
            if e.unique_id == f"{entry.entry_id}_partei_og_jahr_aktuell"
        )
        state = hass.states.get(ent.entity_id)
        assert state is not None
        positionen = state.attributes["positionen"]
        assert len(positionen) == 1
        assert positionen[0]["bezeichnung"] == "Versicherung"
        assert positionen[0]["kategorie"] == Kategorie.VERSICHERUNG

    async def test_haus_kategorie_attributes_list_all_matching_positions(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Haus category sensor shows positions from every party."""
        og = _partei_subentry(subentry_id="og", name="OG", flaeche_qm=85.0)
        dg = _partei_subentry(
            subentry_id="dg",
            name="DG",
            flaeche_qm=65.0,
            bewohnt_ab=date(2019, 1, 1),
        )
        kp = _kp_subentry(
            subentry_id="kp-vers",
            bezeichnung="Versicherung",
            kategorie="versicherung",
            betrag_eur=450.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
        )
        entry = _make_entry(og, dg, kp)
        await _setup_entry(hass, entry)

        registry = er.async_get(hass)
        ent = next(
            e
            for e in registry.entities.values()
            if e.unique_id == f"{entry.entry_id}_haus_kategorie_versicherung_jahr"
        )
        state = hass.states.get(ent.entity_id)
        assert state is not None
        positionen = state.attributes["positionen"]
        # Both parties carry one attribution each for this single cost item.
        assert len(positionen) == 2
        assert state.attributes["kategorie"] == "versicherung"

    async def test_kategorie_sensor_filters_to_its_kategorie(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The party-category sensor only surfaces its own positions."""
        partei = _partei_subentry(subentry_id="og")
        kp_vers = _kp_subentry(
            subentry_id="kp-vers",
            bezeichnung="Versicherung",
            kategorie="versicherung",
            betrag_eur=450.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
        )
        kp_muell = _kp_subentry(
            subentry_id="kp-muell",
            bezeichnung="Muell",
            kategorie="muell",
            betrag_eur=240.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp_vers, kp_muell)
        await _setup_entry(hass, entry)

        registry = er.async_get(hass)
        ent = next(
            e
            for e in registry.entities.values()
            if e.unique_id == f"{entry.entry_id}_partei_og_kategorie_versicherung_jahr"
        )
        state = hass.states.get(ent.entity_id)
        assert state is not None
        positionen = state.attributes["positionen"]
        assert len(positionen) == 1
        assert positionen[0]["bezeichnung"] == "Versicherung"


# ---------------------------------------------------------------------------
# Unique-id guardrails (make_unique_id fallback without category)
# ---------------------------------------------------------------------------


class TestMakeUniqueIdGuardrails:
    """``make_unique_id`` must return a sensible string even without kwargs."""

    def test_partei_kategorie_without_category_returns_prefix(self) -> None:
        uid = ParteiKategorieSensor.make_unique_id("entry-x", "og")
        assert uid == "entry-x_partei_og_kategorie"

    def test_haus_kategorie_without_category_returns_prefix(self) -> None:
        uid = HausKategorieSensor.make_unique_id("entry-x")
        assert uid == "entry-x_haus_kategorie"


# ---------------------------------------------------------------------------
# Vanishing-party availability across all partei sensor classes
# ---------------------------------------------------------------------------


class TestVanishedPartyNativeValues:
    """All party sensors must return None when the party is gone."""

    async def test_all_partei_sensors_return_none_when_party_vanishes(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-vers",
            bezeichnung="Versicherung",
            kategorie="versicherung",
            betrag_eur=450.0,
            periodizitaet="jaehrlich",
            faelligkeit=date(2026, 6, 1),
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        await _setup_entry(hass, entry)

        coord: HauskostenCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        assert coord.data is not None
        coord.data["parteien"].pop("og", None)
        coord.async_update_listeners()
        await hass.async_block_till_done()

        registry = er.async_get(hass)
        for suffix in (
            "monat_aktuell",
            "jahr_aktuell",
            "jahr_budget",
            "naechste_faelligkeit",
            "kategorie_versicherung_jahr",
        ):
            uid = f"{entry.entry_id}_partei_og_{suffix}"
            ent = next(e for e in registry.entities.values() if e.unique_id == uid)
            state = hass.states.get(ent.entity_id)
            assert state is not None
            assert state.state == "unavailable"

    async def test_native_values_return_none_directly_on_vanished_party(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Direct call to native_value returns None when the party is gone.

        Exercises the ``if result is None: return None`` branches inside the
        ``native_value`` properties, which HA normally short-circuits via the
        ``available`` gate.
        """
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-vers",
            bezeichnung="Versicherung",
            kategorie="versicherung",
            betrag_eur=450.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        await _setup_entry(hass, entry)

        coord: HauskostenCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

        # Build standalone sensor instances (not added to HA), then remove
        # the party from coordinator.data so native_value hits the None path.
        monat = ParteiMonatSensor(coord, entry.entry_id, "missing")
        jahr_a = ParteiJahrAktuellSensor(coord, entry.entry_id, "missing")
        jahr_b = ParteiJahrBudgetSensor(coord, entry.entry_id, "missing")
        faellig = ParteiNaechsteFaelligkeitSensor(coord, entry.entry_id, "missing")
        kat = ParteiKategorieSensor(
            coord, entry.entry_id, "missing", Kategorie.VERSICHERUNG
        )
        haus_kat = HausKategorieSensor(coord, entry.entry_id, Kategorie.WARTUNG)

        assert monat.native_value is None
        assert jahr_a.native_value is None
        assert jahr_b.native_value is None
        assert faellig.native_value is None
        assert kat.native_value is None
        # Haus-category sensor with a category absent from pro_kategorie.
        assert haus_kat.native_value is None
        # Attribute path on a vanished party -- positionen is omitted.
        kat_attrs = kat.extra_state_attributes
        assert "positionen" not in kat_attrs
        assert kat_attrs["kategorie"] == "versicherung"


# ---------------------------------------------------------------------------
# Abschlag sensors (phase 4)
# ---------------------------------------------------------------------------


class TestAbschlagSensors:
    """ABSCHLAG positions produce three drill-down sensors per party + house."""

    async def test_abschlag_sensors_created_for_each_position(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Two parties + one abschlag position -> 6 partei + 3 haus sensors."""

        og = _partei_subentry(subentry_id="og", personen=2)
        dg = _partei_subentry(subentry_id="dg", name="DG", flaeche_qm=65.0, personen=1)
        kp = _kp_subentry(
            subentry_id="kp-wasser",
            bezeichnung="Wasser",
            kategorie="wasser",
            zuordnung="haus",
            betragsmodus="abschlag",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            monatlicher_abschlag_eur=50.0,
            abrechnungszeitraum_start=date(2026, 1, 1),
            abrechnungszeitraum_dauer_monate=12,
            verteilung="personen",
        )
        entry = _make_entry(og, dg, kp)

        with (
            patch(
                "custom_components.hauskosten.coordinator.dt_util.now",
                return_value=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
            ),
            patch(
                "custom_components.hauskosten.coordinator."
                "HauskostenCoordinator._fetch_abschlag_verbrauch",
                new=AsyncMock(return_value={"kp-wasser": None}),
            ),
        ):
            await _setup_entry(hass, entry)

        unique_ids = _sensor_unique_ids(hass, entry.entry_id)
        # Per party x position: 3 sensors each = 6 total.
        for pid in ("og", "dg"):
            assert (
                f"{entry.entry_id}_partei_{pid}_abschlag_kp-wasser_gezahlt"
                in unique_ids
            )
            assert f"{entry.entry_id}_partei_{pid}_abschlag_kp-wasser_ist" in unique_ids
            assert (
                f"{entry.entry_id}_partei_{pid}_abschlag_kp-wasser_saldo" in unique_ids
            )
        # Haus x position: 3 aggregate sensors.
        assert f"{entry.entry_id}_haus_abschlag_kp-wasser_gezahlt" in unique_ids
        assert f"{entry.entry_id}_haus_abschlag_kp-wasser_ist" in unique_ids
        assert f"{entry.entry_id}_haus_abschlag_kp-wasser_saldo" in unique_ids

    async def test_abschlag_gezahlt_native_value_distributed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Gezahlt sensor reports the party's share (personen key, 6 months)."""

        og = _partei_subentry(subentry_id="og", personen=2)
        dg = _partei_subentry(subentry_id="dg", name="DG", flaeche_qm=65.0, personen=1)
        kp = _kp_subentry(
            subentry_id="kp-wasser",
            bezeichnung="Wasser",
            kategorie="wasser",
            zuordnung="haus",
            betragsmodus="abschlag",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            monatlicher_abschlag_eur=50.0,
            abrechnungszeitraum_start=date(2026, 1, 1),
            abrechnungszeitraum_dauer_monate=12,
            verteilung="personen",
        )
        entry = _make_entry(og, dg, kp)

        with (
            patch(
                "custom_components.hauskosten.coordinator.dt_util.now",
                return_value=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
            ),
            patch(
                "custom_components.hauskosten.coordinator."
                "HauskostenCoordinator._fetch_abschlag_verbrauch",
                new=AsyncMock(return_value={"kp-wasser": None}),
            ),
        ):
            await _setup_entry(hass, entry)

        registry = er.async_get(hass)
        og_gezahlt = next(
            e
            for e in registry.entities.values()
            if e.unique_id == f"{entry.entry_id}_partei_og_abschlag_kp-wasser_gezahlt"
        )
        state = hass.states.get(og_gezahlt.entity_id)
        assert state is not None
        assert float(state.state) == pytest.approx(200.0)
        # IST sensor has no statistics data -> unavailable.
        og_ist = next(
            e
            for e in registry.entities.values()
            if e.unique_id == f"{entry.entry_id}_partei_og_abschlag_kp-wasser_ist"
        )
        ist_state = hass.states.get(og_ist.entity_id)
        assert ist_state is not None
        assert ist_state.state == "unknown"

    async def test_abschlag_haus_sum_across_parties(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Haus-Gezahlt aggregates the per-party shares back to 300 EUR."""

        og = _partei_subentry(subentry_id="og", personen=2)
        dg = _partei_subentry(subentry_id="dg", name="DG", flaeche_qm=65.0, personen=1)
        kp = _kp_subentry(
            subentry_id="kp-wasser",
            bezeichnung="Wasser",
            kategorie="wasser",
            zuordnung="haus",
            betragsmodus="abschlag",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            monatlicher_abschlag_eur=50.0,
            abrechnungszeitraum_start=date(2026, 1, 1),
            abrechnungszeitraum_dauer_monate=12,
            verteilung="personen",
        )
        entry = _make_entry(og, dg, kp)

        with (
            patch(
                "custom_components.hauskosten.coordinator.dt_util.now",
                return_value=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
            ),
            patch(
                "custom_components.hauskosten.coordinator."
                "HauskostenCoordinator._fetch_abschlag_verbrauch",
                new=AsyncMock(return_value={"kp-wasser": 30.0}),
            ),
        ):
            await _setup_entry(hass, entry)

        registry = er.async_get(hass)
        haus_gezahlt = next(
            e
            for e in registry.entities.values()
            if e.unique_id == f"{entry.entry_id}_haus_abschlag_kp-wasser_gezahlt"
        )
        state = hass.states.get(haus_gezahlt.entity_id)
        assert state is not None
        assert float(state.state) == pytest.approx(300.0)
        # IST total = 3 EUR * 30 m3 + 5 EUR? No -- no grundgebuehr in this test.
        # With einheitspreis=None in this position, IST stays None (no stats
        # formula). Expect unknown state.
        haus_ist = next(
            e
            for e in registry.entities.values()
            if e.unique_id == f"{entry.entry_id}_haus_abschlag_kp-wasser_ist"
        )
        # einheitspreis None -> coordinator skips ist_total, so this stays
        # None. The sensor exposes "unknown".
        assert hass.states.get(haus_ist.entity_id).state == "unknown"

    async def test_abschlag_ist_populated_when_price_and_stats_available(
        self,
        hass: HomeAssistant,
    ) -> None:
        """With einheitspreis + statistics change, IST and Saldo are set."""

        og = _partei_subentry(subentry_id="og", personen=2)
        dg = _partei_subentry(subentry_id="dg", name="DG", flaeche_qm=65.0, personen=1)
        kp = _kp_subentry(
            subentry_id="kp-wasser",
            bezeichnung="Wasser",
            kategorie="wasser",
            zuordnung="haus",
            betragsmodus="abschlag",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            verbrauchs_entity="sensor.wasser",
            einheitspreis_eur=3.0,
            einheit="m3",
            grundgebuehr_eur_monat=5.0,
            monatlicher_abschlag_eur=50.0,
            abrechnungszeitraum_start=date(2026, 1, 1),
            abrechnungszeitraum_dauer_monate=12,
            verteilung="personen",
        )
        entry = _make_entry(og, dg, kp)

        with (
            patch(
                "custom_components.hauskosten.coordinator.dt_util.now",
                return_value=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
            ),
            patch(
                "custom_components.hauskosten.coordinator."
                "HauskostenCoordinator._fetch_abschlag_verbrauch",
                new=AsyncMock(return_value={"kp-wasser": 30.0}),
            ),
        ):
            await _setup_entry(hass, entry)

        registry = er.async_get(hass)
        haus_ist = next(
            e
            for e in registry.entities.values()
            if e.unique_id == f"{entry.entry_id}_haus_abschlag_kp-wasser_ist"
        )
        # IST = 3 EUR/m3 * 30 m3 + 5 EUR * 6 months = 120 EUR (whole house)
        state = hass.states.get(haus_ist.entity_id)
        assert state is not None
        assert float(state.state) == pytest.approx(120.0)
        # Saldo = 120 - 300 = -180 (Guthaben)
        haus_saldo = next(
            e
            for e in registry.entities.values()
            if e.unique_id == f"{entry.entry_id}_haus_abschlag_kp-wasser_saldo"
        )
        saldo_state = hass.states.get(haus_saldo.entity_id)
        assert float(saldo_state.state) == pytest.approx(-180.0)

    async def test_abschlag_sensor_native_value_none_on_vanished_position(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Direct native_value returns None when the position is unknown."""
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-vers",
            bezeichnung="Versicherung",
            kategorie="versicherung",
            betrag_eur=450.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        await _setup_entry(hass, entry)
        coord: HauskostenCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

        gezahlt = ParteiAbschlagGezahltSensor(
            coord, entry.entry_id, "og", "missing-kp", "Phantom"
        )
        ist = ParteiAbschlagIstSensor(
            coord, entry.entry_id, "og", "missing-kp", "Phantom"
        )
        saldo = ParteiAbschlagSaldoSensor(
            coord, entry.entry_id, "og", "missing-kp", "Phantom"
        )
        assert gezahlt.native_value is None
        assert ist.native_value is None
        assert saldo.native_value is None
        # Haus variants without data return None too.
        h_gezahlt = HausAbschlagGezahltSensor(
            coord, entry.entry_id, "missing-kp", "Phantom"
        )
        h_ist = HausAbschlagIstSensor(coord, entry.entry_id, "missing-kp", "Phantom")
        h_saldo = HausAbschlagSaldoSensor(
            coord, entry.entry_id, "missing-kp", "Phantom"
        )
        assert h_gezahlt.native_value is None
        assert h_ist.native_value is None
        assert h_saldo.native_value is None

    async def test_abschlag_sensor_attributes_carry_source_id(
        self,
        hass: HomeAssistant,
    ) -> None:
        """extra_state_attributes expose kostenposition_id + bezeichnung."""

        partei = _partei_subentry(subentry_id="og", personen=2)
        kp = _kp_subentry(
            subentry_id="kp-w",
            bezeichnung="Wasser",
            kategorie="wasser",
            zuordnung="haus",
            betragsmodus="abschlag",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            monatlicher_abschlag_eur=50.0,
            abrechnungszeitraum_start=date(2026, 1, 1),
            abrechnungszeitraum_dauer_monate=12,
            verteilung="gleich",
        )
        entry = _make_entry(partei, kp)
        with (
            patch(
                "custom_components.hauskosten.coordinator.dt_util.now",
                return_value=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
            ),
            patch(
                "custom_components.hauskosten.coordinator."
                "HauskostenCoordinator._fetch_abschlag_verbrauch",
                new=AsyncMock(return_value={"kp-w": None}),
            ),
        ):
            await _setup_entry(hass, entry)
        coord: HauskostenCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        gezahlt = ParteiAbschlagGezahltSensor(
            coord, entry.entry_id, "og", "kp-w", "Wasser"
        )
        attrs = gezahlt.extra_state_attributes
        assert attrs["kostenposition_id"] == "kp-w"
        assert attrs["bezeichnung"] == "Wasser"
        haus_gezahlt = HausAbschlagGezahltSensor(
            coord, entry.entry_id, "kp-w", "Wasser"
        )
        h_attrs = haus_gezahlt.extra_state_attributes
        assert h_attrs["kostenposition_id"] == "kp-w"
        assert h_attrs["bezeichnung"] == "Wasser"
