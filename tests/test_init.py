"""Tests for custom_components.hauskosten.__init__.

Authoritative oracles:
* docs/ARCHITECTURE.md -- Startup / shutdown lifecycle, state-change listener
  wiring, service action contracts.
* docs/DATA_MODEL.md   -- Schema-version migration semantics.
* AGENTS.md hard constraints:
    #3 file I/O only via the Storage API,
    #4 every ``async`` error path logs via ``_LOGGER``.

The tests drive the integration through the public HA setup / unload APIs so
we implicitly verify that platform forwarding, data-slot population and the
update listener are wired correctly.
"""

from __future__ import annotations

from datetime import date
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError

from custom_components.hauskosten import async_migrate_entry, async_setup_entry
from custom_components.hauskosten.const import (
    CONF_SCHEMA_VERSION,
    DOMAIN,
    SERVICE_ADD_EINMALIG,
    SERVICE_MARK_PAID,
    SUBENTRY_KOSTENPOSITION,
    SUBENTRY_PARTEI,
)
from custom_components.hauskosten.coordinator import HauskostenCoordinator
from custom_components.hauskosten.services import _resolve_entry_slot
from custom_components.hauskosten.storage import HauskostenStore

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
) -> ConfigSubentry:
    """Build a ConfigSubentry of type 'partei'."""
    data: dict[str, Any] = {
        "name": name,
        "flaeche_qm": flaeche_qm,
        "personen": personen,
        "bewohnt_ab": bewohnt_ab.isoformat(),
        "bewohnt_bis": None,
        "hinweis": None,
    }
    return ConfigSubentry(
        data=MappingProxyType(data),
        subentry_id=subentry_id,
        subentry_type=SUBENTRY_PARTEI,
        title=name,
        unique_id=None,
    )


def _make_entry(
    *subentries: ConfigSubentry,
    version: int = 1,
    entry_id: str = "entry-init-test",
) -> MockConfigEntry:
    """Build a MockConfigEntry with the given subentries and version."""
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
        title="Haus",
        data={"haus_name": "Haus"},
        entry_id=entry_id,
        version=version,
        subentries_data=subentries_data,
    )


async def _setup_entry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
) -> bool:
    """Add entry to hass, run setup, wait for tasks, return setup result."""
    entry.add_to_hass(hass)
    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return result


# ---------------------------------------------------------------------------
# async_setup_entry -- happy path
# ---------------------------------------------------------------------------


class TestSetupEntry:
    """The happy path must wire store + coordinator + listener + platforms."""

    async def test_setup_succeeds_with_minimal_entry(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Empty entry (no subentries) still sets up cleanly."""
        entry = _make_entry()
        result = await _setup_entry(hass, entry)
        assert result is True
        assert entry.state is ConfigEntryState.LOADED

    async def test_setup_populates_hass_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """hass.data[DOMAIN][entry_id] carries coordinator and store."""
        entry = _make_entry(_partei_subentry())
        await _setup_entry(hass, entry)

        assert DOMAIN in hass.data
        slot = hass.data[DOMAIN][entry.entry_id]
        assert isinstance(slot["coordinator"], HauskostenCoordinator)
        assert isinstance(slot["store"], HauskostenStore)

    async def test_setup_performs_first_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        """After setup the coordinator has computed its initial snapshot."""
        entry = _make_entry(_partei_subentry())
        await _setup_entry(hass, entry)

        coord: HauskostenCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        assert coord.data is not None
        assert "partei-og" in coord.data["parteien"]

    async def test_setup_registers_state_listener_for_verbrauch(
        self,
        hass: HomeAssistant,
    ) -> None:
        """With a verbrauch kostenposition the state listener is active."""
        partei = _partei_subentry()
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

        coord: HauskostenCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        # Changing the tracked entity should trigger a refresh.
        assert coord.data["parteien"]["partei-og"]["jahr_budget_eur"] == 120.0

        hass.states.async_set("sensor.wasser", "60")
        await hass.async_block_till_done()
        await hass.async_block_till_done()

        assert coord.data["parteien"]["partei-og"]["jahr_budget_eur"] == 180.0


# ---------------------------------------------------------------------------
# async_setup_entry -- failure paths
# ---------------------------------------------------------------------------


class TestSetupEntryFailures:
    """Failure modes must surface as ConfigEntryNotReady."""

    async def test_store_load_failure_raises_not_ready(
        self,
        hass: HomeAssistant,
    ) -> None:
        """If the store cannot load, setup raises ConfigEntryNotReady."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        with (
            patch(
                "custom_components.hauskosten.HauskostenStore.async_load",
                new=AsyncMock(side_effect=OSError("disk failure")),
            ),
            pytest.raises(ConfigEntryNotReady),
        ):
            await async_setup_entry(hass, entry)


# ---------------------------------------------------------------------------
# async_unload_entry
# ---------------------------------------------------------------------------


class TestUnloadEntry:
    """Unloading must tear down listener and clean up hass.data."""

    async def test_unload_removes_hass_data_slot(
        self,
        hass: HomeAssistant,
    ) -> None:
        entry = _make_entry(_partei_subentry())
        await _setup_entry(hass, entry)
        assert entry.entry_id in hass.data[DOMAIN]

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.entry_id not in hass.data.get(DOMAIN, {})

    async def test_unload_shuts_down_listener(
        self,
        hass: HomeAssistant,
    ) -> None:
        """After unload the coordinator's state listener is gone."""
        entry = _make_entry(_partei_subentry())
        await _setup_entry(hass, entry)
        coord: HauskostenCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        # Prime a listener so we can observe the teardown.
        coord.async_setup_state_listener()

        assert await hass.config_entries.async_unload(entry.entry_id)
        # Accessing the private attribute is fine for this regression test --
        # we verify the listener slot is cleared by async_shutdown_listener.
        assert coord._unsub_state_listener is None


# ---------------------------------------------------------------------------
# update listener -- subentry / entry updates
# ---------------------------------------------------------------------------


class TestUpdateListener:
    """Config updates must re-sync the coordinator and request a refresh."""

    async def test_subentry_change_triggers_coordinator_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Updating the entry triggers re-setup of the state listener + refresh."""
        entry = _make_entry(_partei_subentry())
        await _setup_entry(hass, entry)
        coord: HauskostenCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

        # Count how often the coordinator requested a refresh.
        refresh_calls: list[bool] = []
        original_request = coord.async_request_refresh

        async def counting_refresh() -> None:
            refresh_calls.append(True)
            await original_request()

        with patch.object(
            coord,
            "async_request_refresh",
            side_effect=counting_refresh,
        ):
            hass.config_entries.async_update_entry(entry, title="Haus (renamed)")
            await hass.async_block_till_done()

        assert len(refresh_calls) >= 1


# ---------------------------------------------------------------------------
# async_migrate_entry
# ---------------------------------------------------------------------------


class TestMigrateEntry:
    """Schema-version migrations: current version passes, future blocks."""

    async def test_current_version_migration_succeeds(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Version == CONF_SCHEMA_VERSION returns True (no-op)."""
        entry = _make_entry(version=CONF_SCHEMA_VERSION)
        entry.add_to_hass(hass)
        assert await async_migrate_entry(hass, entry) is True

    async def test_downgrade_is_rejected(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Version > current schema returns False (downgrade unsupported)."""
        entry = _make_entry(version=CONF_SCHEMA_VERSION + 1)
        entry.add_to_hass(hass)
        assert await async_migrate_entry(hass, entry) is False


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


class TestServices:
    """The add_einmalig and mark_paid services must be registered on setup."""

    async def test_services_registered_after_setup(
        self,
        hass: HomeAssistant,
    ) -> None:
        entry = _make_entry(_partei_subentry())
        await _setup_entry(hass, entry)

        assert hass.services.has_service(DOMAIN, SERVICE_ADD_EINMALIG)
        assert hass.services.has_service(DOMAIN, SERVICE_MARK_PAID)

    async def test_add_einmalig_appends_record_and_refreshes(
        self,
        hass: HomeAssistant,
    ) -> None:
        entry = _make_entry(_partei_subentry())
        await _setup_entry(hass, entry)
        store: HauskostenStore = hass.data[DOMAIN][entry.entry_id]["store"]

        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_EINMALIG,
            {
                "entry_id": entry.entry_id,
                "bezeichnung": "Handwerker",
                "kategorie": "sonstiges",
                "betrag_eur": 150.0,
                "datum": date(2026, 3, 1).isoformat(),
                "zuordnung": "haus",
                "verteilung": "flaeche",
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        assert len(store.adhoc_kosten) == 1
        assert store.adhoc_kosten[0]["bezeichnung"] == "Handwerker"

    async def test_mark_paid_records_timestamp(
        self,
        hass: HomeAssistant,
    ) -> None:
        entry = _make_entry(_partei_subentry())
        await _setup_entry(hass, entry)
        store: HauskostenStore = hass.data[DOMAIN][entry.entry_id]["store"]

        await hass.services.async_call(
            DOMAIN,
            SERVICE_MARK_PAID,
            {
                "entry_id": entry.entry_id,
                "kostenposition_id": "kp-xyz",
                "bezahlt_am": date(2026, 4, 1).isoformat(),
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        assert store.paid_records.get("kp-xyz") == date(2026, 4, 1)

    async def test_unknown_entry_id_raises(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Calling a service with an unknown entry_id surfaces an error."""
        entry = _make_entry(_partei_subentry())
        await _setup_entry(hass, entry)

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_MARK_PAID,
                {
                    "entry_id": "does-not-exist",
                    "kostenposition_id": "kp-xyz",
                    "bezahlt_am": date(2026, 4, 1).isoformat(),
                },
                blocking=True,
            )

    async def test_services_unregister_on_last_entry_unload(
        self,
        hass: HomeAssistant,
    ) -> None:
        """When the last entry unloads, services are removed to keep HA tidy."""
        entry = _make_entry(_partei_subentry())
        await _setup_entry(hass, entry)
        assert hass.services.has_service(DOMAIN, SERVICE_ADD_EINMALIG)

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert not hass.services.has_service(DOMAIN, SERVICE_ADD_EINMALIG)
        assert not hass.services.has_service(DOMAIN, SERVICE_MARK_PAID)

    async def test_services_remain_when_second_entry_still_loaded(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Unloading one of two entries must keep services for the other."""
        entry_a = _make_entry(_partei_subentry(), entry_id="entry-a")
        entry_b = _make_entry(_partei_subentry(), entry_id="entry-b")
        await _setup_entry(hass, entry_a)
        await _setup_entry(hass, entry_b)

        await hass.config_entries.async_unload(entry_a.entry_id)
        await hass.async_block_till_done()

        assert hass.services.has_service(DOMAIN, SERVICE_ADD_EINMALIG)
        assert hass.services.has_service(DOMAIN, SERVICE_MARK_PAID)

    async def test_add_einmalig_auto_picks_single_entry(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Omitting entry_id works when exactly one entry is loaded."""
        entry = _make_entry(_partei_subentry())
        await _setup_entry(hass, entry)
        store: HauskostenStore = hass.data[DOMAIN][entry.entry_id]["store"]

        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_EINMALIG,
            {
                "bezeichnung": "Handwerker",
                "kategorie": "sonstiges",
                "betrag_eur": 150.0,
                "datum": date(2026, 3, 1).isoformat(),
                "zuordnung": "haus",
                "verteilung": "flaeche",
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        assert len(store.adhoc_kosten) == 1

    async def test_missing_entry_id_with_multiple_entries_raises(
        self,
        hass: HomeAssistant,
    ) -> None:
        """When multiple entries exist, entry_id is required."""
        entry_a = _make_entry(_partei_subentry(), entry_id="entry-a")
        entry_b = _make_entry(_partei_subentry(), entry_id="entry-b")
        await _setup_entry(hass, entry_a)
        await _setup_entry(hass, entry_b)

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_MARK_PAID,
                {
                    "kostenposition_id": "kp-xyz",
                    "bezahlt_am": date(2026, 4, 1).isoformat(),
                },
                blocking=True,
            )

    async def test_resolve_entry_slot_without_entries_raises(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Direct helper call with no loaded entries raises with a helpful msg."""
        # Make sure the domain slot is empty.
        hass.data.pop(DOMAIN, None)
        with pytest.raises(ServiceValidationError, match="no loaded"):
            _resolve_entry_slot(hass, None)

    async def test_add_einmalig_duplicate_id_raises(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A ValueError from the store surfaces as ServiceValidationError."""
        entry = _make_entry(_partei_subentry())
        await _setup_entry(hass, entry)
        store: HauskostenStore = hass.data[DOMAIN][entry.entry_id]["store"]
        # Pre-seed with a record, then patch uuid.uuid4 to generate the
        # same id so async_add_adhoc raises ValueError.
        await store.async_add_adhoc(
            {
                "id": "fixed-id",
                "bezeichnung": "pre-existing",
                "kategorie": "sonstiges",
                "betrag_eur": 100.0,
                "datum": date(2026, 3, 1),
                "zuordnung": "haus",
                "zuordnung_partei_id": None,
                "verteilung": "flaeche",
                "bezahlt_am": None,
                "notiz": None,
            }
        )
        with (
            patch(
                "custom_components.hauskosten.services.uuid.uuid4",
                return_value="fixed-id",
            ),
            pytest.raises(ServiceValidationError),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_ADD_EINMALIG,
                {
                    "bezeichnung": "duplicate",
                    "kategorie": "sonstiges",
                    "betrag_eur": 50.0,
                    "datum": date(2026, 3, 1).isoformat(),
                    "zuordnung": "haus",
                    "verteilung": "flaeche",
                },
                blocking=True,
            )
