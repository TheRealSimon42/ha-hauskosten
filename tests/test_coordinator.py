"""Tests for custom_components.hauskosten.coordinator.

Authoritative oracles:
* docs/ARCHITECTURE.md -- "Coordinator (coordinator.py)" section: the update
  cycle, state-change listener, pure aggregation.
* docs/DATA_MODEL.md   -- ``CoordinatorData`` / ``ParteiResult`` /
  ``HausResult`` / ``PositionAttribution`` shapes.
* docs/DISTRIBUTION.md -- allocation formulas invoked via distribution.allocate.
* AGENTS.md hard constraints:
    #1 no relogging of consumption entities (verified indirectly: we only read
       state),
    #3 no I/O in the update callback (the coordinator only reads entity state;
       storage I/O goes through :class:`HauskostenStore`),
    #4 every async error path goes through ``_LOGGER``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.hauskosten.const import (
    DOMAIN,
    SUBENTRY_KOSTENPOSITION,
    SUBENTRY_PARTEI,
)
from custom_components.hauskosten.coordinator import HauskostenCoordinator
from custom_components.hauskosten.models import Kategorie
from custom_components.hauskosten.storage import HauskostenStore

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _partei_subentry(
    *,
    subentry_id: str = "partei-og",
    name: str = "OG (Simon)",
    flaeche_qm: float = 85.0,
    personen: int = 2,
    bewohnt_ab: date = date(2020, 1, 1),
    bewohnt_bis: date | None = None,
    hinweis: str | None = None,
) -> ConfigSubentry:
    """Build a ConfigSubentry of type 'partei' for use in MockConfigEntry."""
    data: dict[str, Any] = {
        "name": name,
        "flaeche_qm": flaeche_qm,
        "personen": personen,
        "bewohnt_ab": bewohnt_ab.isoformat(),
        "bewohnt_bis": bewohnt_bis.isoformat() if bewohnt_bis else None,
        "hinweis": hinweis,
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
    verteilung: str = "flaeche",
    verbrauch_entities_pro_partei: dict[str, str] | None = None,
    aktiv_ab: date | None = None,
    aktiv_bis: date | None = None,
    notiz: str | None = None,
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
        "verteilung": verteilung,
        "verbrauch_entities_pro_partei": verbrauch_entities_pro_partei,
        "aktiv_ab": aktiv_ab.isoformat() if aktiv_ab else None,
        "aktiv_bis": aktiv_bis.isoformat() if aktiv_bis else None,
        "notiz": notiz,
    }
    return ConfigSubentry(
        data=MappingProxyType(data),
        subentry_id=subentry_id,
        subentry_type=SUBENTRY_KOSTENPOSITION,
        title=bezeichnung,
        unique_id=None,
    )


def _make_entry(*subentries: ConfigSubentry) -> MockConfigEntry:
    """Build a MockConfigEntry carrying the given subentries."""
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
        data={},
        entry_id="entry-coord-test",
        subentries_data=subentries_data,
    )


async def _make_coordinator(
    hass: HomeAssistant,
    entry: MockConfigEntry,
) -> HauskostenCoordinator:
    """Add the entry to hass, build the coordinator and return it."""
    entry.add_to_hass(hass)
    store = HauskostenStore(hass, entry.entry_id)
    await store.async_load()
    return HauskostenCoordinator(hass, entry, store)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestCoordinatorConstruction:
    """Instantiation basics."""

    async def test_default_update_interval_is_30_minutes(
        self,
        hass: HomeAssistant,
    ) -> None:
        entry = _make_entry()
        coord = await _make_coordinator(hass, entry)
        assert coord.update_interval is not None
        assert coord.update_interval.total_seconds() == 30 * 60

    async def test_name_includes_entry_title(
        self,
        hass: HomeAssistant,
    ) -> None:
        entry = _make_entry()
        coord = await _make_coordinator(hass, entry)
        assert "Haus" in coord.name

    async def test_config_entry_attached(
        self,
        hass: HomeAssistant,
    ) -> None:
        entry = _make_entry()
        coord = await _make_coordinator(hass, entry)
        assert coord.config_entry is entry


# ---------------------------------------------------------------------------
# Update pipeline -- empty entry
# ---------------------------------------------------------------------------


class TestUpdateEmptyEntry:
    """Entries without parteien / kostenpositionen produce zeroed output."""

    async def test_empty_entry_produces_zero_budget(
        self,
        hass: HomeAssistant,
    ) -> None:
        entry = _make_entry()
        coord = await _make_coordinator(hass, entry)
        data = await coord._async_update_data()

        assert data["parteien"] == {}
        assert data["haus"]["jahr_budget_eur"] == 0.0
        assert data["haus"]["jahr_aktuell_eur"] == 0.0
        assert data["haus"]["pro_kategorie_jahr_eur"] == {}

    async def test_computed_at_is_timezone_aware(
        self,
        hass: HomeAssistant,
    ) -> None:
        entry = _make_entry()
        coord = await _make_coordinator(hass, entry)
        data = await coord._async_update_data()

        assert isinstance(data["computed_at"], datetime)
        assert data["computed_at"].tzinfo is not None

    async def test_jahr_and_monat_match_now(
        self,
        hass: HomeAssistant,
    ) -> None:
        entry = _make_entry()
        coord = await _make_coordinator(hass, entry)

        fixed = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
        with patch(
            "custom_components.hauskosten.coordinator.dt_util.now",
            return_value=fixed,
        ):
            data = await coord._async_update_data()

        assert data["jahr"] == 2026
        assert data["monat"] == 7


# ---------------------------------------------------------------------------
# Update pipeline -- pauschal
# ---------------------------------------------------------------------------


class TestPauschalDistribution:
    """Pauschal costs are annualised and distributed per the verteilung key."""

    async def test_single_partei_gets_full_annual_amount(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="partei-og")
        kp = _kp_subentry(
            subentry_id="kp-vers",
            bezeichnung="Versicherung",
            kategorie="versicherung",
            zuordnung="haus",
            betrag_eur=450.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        coord = await _make_coordinator(hass, entry)

        data = await coord._async_update_data()
        party = data["parteien"]["partei-og"]

        assert party["jahr_budget_eur"] == pytest.approx(450.0)
        assert party["pro_kategorie_jahr_eur"][Kategorie.VERSICHERUNG] == pytest.approx(
            450.0
        )
        assert len(party["positionen"]) == 1
        assert party["positionen"][0]["anteil_eur_jahr"] == pytest.approx(450.0)
        assert party["positionen"][0]["error"] is None
        assert data["haus"]["jahr_budget_eur"] == pytest.approx(450.0)

    async def test_flaeche_distribution_two_parteien(
        self,
        hass: HomeAssistant,
    ) -> None:
        # OG 85 m2, DG 65 m2, Versicherung 450 EUR -> 255 / 195 (flaeche-weighted)
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
        coord = await _make_coordinator(hass, entry)
        fixed = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        with patch(
            "custom_components.hauskosten.coordinator.dt_util.now",
            return_value=fixed,
        ):
            data = await coord._async_update_data()

        assert data["parteien"]["og"]["jahr_budget_eur"] == pytest.approx(255.0)
        assert data["parteien"]["dg"]["jahr_budget_eur"] == pytest.approx(195.0)
        assert data["haus"]["jahr_budget_eur"] == pytest.approx(450.0)

    async def test_monat_aktuell_is_twelfth_of_jahr(
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
        coord = await _make_coordinator(hass, entry)
        data = await coord._async_update_data()

        assert data["parteien"]["og"]["monat_aktuell_eur"] == pytest.approx(100.0)

    async def test_naechste_faelligkeit_is_earliest_active_due_date(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp_march = _kp_subentry(
            subentry_id="kp-m",
            bezeichnung="Versicherung",
            betrag_eur=450.0,
            periodizitaet="jaehrlich",
            faelligkeit=date(2026, 3, 15),
            verteilung="flaeche",
        )
        kp_january = _kp_subentry(
            subentry_id="kp-j",
            bezeichnung="Grundsteuer",
            kategorie="grund",
            betrag_eur=400.0,
            periodizitaet="jaehrlich",
            faelligkeit=date(2026, 1, 15),
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp_march, kp_january)
        coord = await _make_coordinator(hass, entry)
        fixed = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
        with patch(
            "custom_components.hauskosten.coordinator.dt_util.now",
            return_value=fixed,
        ):
            data = await coord._async_update_data()

        assert data["parteien"]["og"]["naechste_faelligkeit"] == date(2026, 1, 15)


# ---------------------------------------------------------------------------
# Update pipeline -- verbrauch
# ---------------------------------------------------------------------------


class TestVerbrauchDistribution:
    """Verbrauch positions read entity state and multiply with einheitspreis."""

    async def test_verbrauch_entity_is_read_and_multiplied(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-wasser",
            bezeichnung="Wasser",
            kategorie="wasser",
            betragsmodus="verbrauch",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            verbrauchs_entity="sensor.wasser",
            einheitspreis_eur=3.0,
            einheit="m3",
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        hass.states.async_set("sensor.wasser", "40")
        coord = await _make_coordinator(hass, entry)
        data = await coord._async_update_data()

        # 40 m3 * 3 EUR = 120 EUR
        party = data["parteien"]["og"]
        assert party["jahr_budget_eur"] == pytest.approx(120.0)
        assert party["positionen"][0]["error"] is None

    async def test_missing_verbrauch_entity_marks_error(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-wasser",
            bezeichnung="Wasser",
            kategorie="wasser",
            betragsmodus="verbrauch",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            verbrauchs_entity="sensor.missing",
            einheitspreis_eur=3.0,
            einheit="m3",
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        coord = await _make_coordinator(hass, entry)
        with caplog.at_level("WARNING"):
            data = await coord._async_update_data()

        party = data["parteien"]["og"]
        # Missing entity -> attribution carries an error, not 0 EUR
        assert party["positionen"][0]["error"] is not None
        assert party["jahr_budget_eur"] == 0.0
        assert any("sensor.missing" in record.message for record in caplog.records)

    async def test_unavailable_state_marks_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-wasser",
            bezeichnung="Wasser",
            kategorie="wasser",
            betragsmodus="verbrauch",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            verbrauchs_entity="sensor.wasser",
            einheitspreis_eur=3.0,
            einheit="m3",
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        hass.states.async_set("sensor.wasser", "unavailable")
        coord = await _make_coordinator(hass, entry)
        data = await coord._async_update_data()

        assert data["parteien"]["og"]["positionen"][0]["error"] is not None

    async def test_non_numeric_state_marks_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-wasser",
            bezeichnung="Wasser",
            kategorie="wasser",
            betragsmodus="verbrauch",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            verbrauchs_entity="sensor.wasser",
            einheitspreis_eur=3.0,
            einheit="m3",
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        hass.states.async_set("sensor.wasser", "nope")
        coord = await _make_coordinator(hass, entry)
        data = await coord._async_update_data()

        assert data["parteien"]["og"]["positionen"][0]["error"] is not None


# ---------------------------------------------------------------------------
# Mieterwechsel
# ---------------------------------------------------------------------------


class TestMieterwechsel:
    """Two parties with overlapping tenancy intervals must weight correctly."""

    async def test_time_weighted_annual_allocation(
        self,
        hass: HomeAssistant,
    ) -> None:
        # OG: full year (365 days). DG_alt: Jan 1 .. Jun 30 (181 days).
        # DG_neu: Jul 1 .. Dec 31 (184 days). Versicherung 450 EUR / flaeche.
        # Matches the example table in docs/DISTRIBUTION.md.
        og = _partei_subentry(
            subentry_id="og",
            name="OG",
            flaeche_qm=85.0,
            bewohnt_ab=date(2019, 1, 1),
        )
        dg_alt = _partei_subentry(
            subentry_id="dg-alt",
            name="DG_alt",
            flaeche_qm=65.0,
            bewohnt_ab=date(2019, 1, 1),
            bewohnt_bis=date(2026, 6, 30),
        )
        dg_neu = _partei_subentry(
            subentry_id="dg-neu",
            name="DG_neu",
            flaeche_qm=65.0,
            bewohnt_ab=date(2026, 7, 1),
        )
        kp = _kp_subentry(
            subentry_id="kp-vers",
            bezeichnung="Versicherung",
            betrag_eur=450.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
        )
        entry = _make_entry(og, dg_alt, dg_neu, kp)
        coord = await _make_coordinator(hass, entry)
        fixed = datetime(2026, 6, 15, 0, 0, tzinfo=UTC)
        with patch(
            "custom_components.hauskosten.coordinator.dt_util.now",
            return_value=fixed,
        ):
            data = await coord._async_update_data()

        # Expected shares follow docs/DISTRIBUTION.md.
        assert data["parteien"]["og"]["jahr_budget_eur"] == pytest.approx(255.0)
        assert data["parteien"]["dg-alt"]["jahr_budget_eur"] == pytest.approx(
            96.69, rel=1e-2
        )
        assert data["parteien"]["dg-neu"]["jahr_budget_eur"] == pytest.approx(
            98.31, rel=1e-2
        )


# ---------------------------------------------------------------------------
# Ad-hoc kosten
# ---------------------------------------------------------------------------


class TestAdhocKosten:
    """Ad-hoc costs from the store are added on top of subentry-based costs."""

    async def test_adhoc_added_to_totals(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        entry = _make_entry(partei)
        entry.add_to_hass(hass)
        store = HauskostenStore(hass, entry.entry_id)
        await store.async_load()
        await store.async_add_adhoc(
            {
                "id": "ah-1",
                "bezeichnung": "Handwerker",
                "kategorie": "sonstiges",
                "betrag_eur": 150.0,
                "datum": date(2026, 3, 1),
                "zuordnung": "haus",
                "zuordnung_partei_id": None,
                "verteilung": "flaeche",
                "bezahlt_am": None,
                "notiz": None,
            }
        )
        coord = HauskostenCoordinator(hass, entry, store)

        fixed = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        with patch(
            "custom_components.hauskosten.coordinator.dt_util.now",
            return_value=fixed,
        ):
            data = await coord._async_update_data()

        assert data["parteien"]["og"]["jahr_budget_eur"] == pytest.approx(150.0)
        assert data["parteien"]["og"]["pro_kategorie_jahr_eur"][
            Kategorie.SONSTIGES
        ] == pytest.approx(150.0)


# ---------------------------------------------------------------------------
# State change listener
# ---------------------------------------------------------------------------


class TestStateChangeListener:
    """The coordinator refreshes on state changes of referenced entities."""

    async def test_state_change_triggers_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-wasser",
            bezeichnung="Wasser",
            kategorie="wasser",
            betragsmodus="verbrauch",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            verbrauchs_entity="sensor.wasser",
            einheitspreis_eur=3.0,
            einheit="m3",
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        hass.states.async_set("sensor.wasser", "40")
        coord = await _make_coordinator(hass, entry)

        await coord.async_refresh()
        coord.async_setup_state_listener()

        first_budget = coord.data["parteien"]["og"]["jahr_budget_eur"]
        assert first_budget == pytest.approx(120.0)

        hass.states.async_set("sensor.wasser", "60")
        await hass.async_block_till_done()
        # Debouncer schedules -- wait for it to settle.
        await hass.async_block_till_done()

        assert coord.data["parteien"]["og"]["jahr_budget_eur"] == pytest.approx(180.0)

        coord.async_shutdown_listener()

    async def test_shutdown_listener_is_idempotent(
        self,
        hass: HomeAssistant,
    ) -> None:
        entry = _make_entry()
        coord = await _make_coordinator(hass, entry)
        coord.async_setup_state_listener()
        coord.async_shutdown_listener()
        # Second call must be a no-op (no raise)
        coord.async_shutdown_listener()

    async def test_setup_listener_without_tracked_entities(
        self,
        hass: HomeAssistant,
    ) -> None:
        """With no verbrauch entities, the listener is not installed."""
        entry = _make_entry(_partei_subentry(subentry_id="og"))
        coord = await _make_coordinator(hass, entry)
        coord.async_setup_state_listener()
        # Nothing crashes; shutting down is safe.
        coord.async_shutdown_listener()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Failure modes: unexpected exceptions surface as UpdateFailed."""

    async def test_unexpected_exception_becomes_update_failed(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        entry = _make_entry(_partei_subentry(subentry_id="og"))
        coord = await _make_coordinator(hass, entry)

        def boom(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("kaboom")

        monkeypatch.setattr(
            "custom_components.hauskosten.coordinator.effektive_tage",
            boom,
        )
        with caplog.at_level("ERROR"), pytest.raises(UpdateFailed):
            await coord._async_update_data()
        assert "kaboom" in caplog.text or "unexpected" in caplog.text.lower()

    async def test_distribution_error_marks_position(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A distribution ValueError marks the position error, not the whole run."""
        partei = _partei_subentry(subentry_id="og")
        # DIREKT without valid zuordnung_partei_id -> distribution.allocate raises.
        kp = _kp_subentry(
            subentry_id="kp-bad",
            bezeichnung="Grundgebuehr",
            kategorie="strom",
            zuordnung="partei",
            zuordnung_partei_id="does-not-exist",
            betrag_eur=120.0,
            periodizitaet="jaehrlich",
            verteilung="direkt",
        )
        entry = _make_entry(partei, kp)
        coord = await _make_coordinator(hass, entry)
        data = await coord._async_update_data()

        party = data["parteien"]["og"]
        errors = [p for p in party["positionen"] if p["error"] is not None]
        assert len(errors) == 1
        assert party["jahr_budget_eur"] == 0.0


# ---------------------------------------------------------------------------
# Seasonal activity
# ---------------------------------------------------------------------------


class TestSeasonalActivity:
    """Kostenpositionen with aktiv_ab/aktiv_bis outside the current year are skipped."""

    async def test_inactive_kostenposition_is_ignored(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp_active = _kp_subentry(
            subentry_id="kp-active",
            bezeichnung="Versicherung",
            betrag_eur=100.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
        )
        kp_inactive = _kp_subentry(
            subentry_id="kp-inactive",
            bezeichnung="Alt",
            betrag_eur=999.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
            aktiv_ab=date(2000, 1, 1),
            aktiv_bis=date(2000, 12, 31),
        )
        entry = _make_entry(partei, kp_active, kp_inactive)
        coord = await _make_coordinator(hass, entry)
        fixed = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
        with patch(
            "custom_components.hauskosten.coordinator.dt_util.now",
            return_value=fixed,
        ):
            data = await coord._async_update_data()

        assert data["parteien"]["og"]["jahr_budget_eur"] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Partei not yet bewohnt
# ---------------------------------------------------------------------------


class TestInactivePartei:
    """A partei whose bewohnt_ab is in the future carries no cost for this year."""

    async def test_future_partei_is_inactive(
        self,
        hass: HomeAssistant,
    ) -> None:
        og = _partei_subentry(subentry_id="og", flaeche_qm=85.0)
        future = _partei_subentry(
            subentry_id="future",
            name="Future",
            flaeche_qm=65.0,
            bewohnt_ab=date(2099, 1, 1),
        )
        kp = _kp_subentry(
            subentry_id="kp-vers",
            bezeichnung="Versicherung",
            betrag_eur=450.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
        )
        entry = _make_entry(og, future, kp)
        coord = await _make_coordinator(hass, entry)
        fixed = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
        with patch(
            "custom_components.hauskosten.coordinator.dt_util.now",
            return_value=fixed,
        ):
            data = await coord._async_update_data()

        # Future partei has 0 days active; OG absorbs the whole 450 EUR.
        assert data["parteien"]["og"]["jahr_budget_eur"] == pytest.approx(450.0)
        assert data["parteien"]["future"]["jahr_budget_eur"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Verbrauch subzaehler
# ---------------------------------------------------------------------------


class TestVerbrauchSubzaehler:
    """Subzaehler distribution uses per-party verbrauch entities."""

    async def test_subzaehler_distribution(
        self,
        hass: HomeAssistant,
    ) -> None:
        og = _partei_subentry(subentry_id="og", flaeche_qm=85.0)
        dg = _partei_subentry(
            subentry_id="dg",
            name="DG",
            flaeche_qm=65.0,
            bewohnt_ab=date(2019, 1, 1),
        )
        kp = _kp_subentry(
            subentry_id="kp-wasser",
            bezeichnung="Wasser",
            kategorie="wasser",
            betragsmodus="verbrauch",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            verbrauchs_entity="sensor.wasser_gesamt",
            einheitspreis_eur=3.0,
            einheit="m3",
            verteilung="verbrauch",
            verbrauch_entities_pro_partei={
                "og": "sensor.wasser_og",
                "dg": "sensor.wasser_dg",
            },
        )
        entry = _make_entry(og, dg, kp)
        hass.states.async_set("sensor.wasser_gesamt", "100")
        hass.states.async_set("sensor.wasser_og", "70")
        hass.states.async_set("sensor.wasser_dg", "30")
        coord = await _make_coordinator(hass, entry)
        data = await coord._async_update_data()

        # Total amount: 100 m3 * 3 EUR = 300 EUR
        # Split 70/30 -> OG 210, DG 90
        assert data["parteien"]["og"]["jahr_budget_eur"] == pytest.approx(210.0)
        assert data["parteien"]["dg"]["jahr_budget_eur"] == pytest.approx(90.0)

    async def test_subzaehler_missing_entity_marks_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        og = _partei_subentry(subentry_id="og", flaeche_qm=85.0)
        dg = _partei_subentry(
            subentry_id="dg",
            name="DG",
            flaeche_qm=65.0,
            bewohnt_ab=date(2019, 1, 1),
        )
        kp = _kp_subentry(
            subentry_id="kp-wasser",
            bezeichnung="Wasser",
            kategorie="wasser",
            betragsmodus="verbrauch",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            verbrauchs_entity="sensor.wasser_gesamt",
            einheitspreis_eur=3.0,
            einheit="m3",
            verteilung="verbrauch",
            verbrauch_entities_pro_partei={
                "og": "sensor.wasser_og",
                "dg": "sensor.wasser_dg_missing",
            },
        )
        entry = _make_entry(og, dg, kp)
        hass.states.async_set("sensor.wasser_gesamt", "100")
        hass.states.async_set("sensor.wasser_og", "70")
        coord = await _make_coordinator(hass, entry)
        data = await coord._async_update_data()

        # With a missing sub-meter, the position errors out.
        assert data["parteien"]["og"]["positionen"][0]["error"] is not None


# ---------------------------------------------------------------------------
# Relevant entities
# ---------------------------------------------------------------------------


class TestRelevantEntities:
    """The helper collecting tracked entity IDs drives the listener scope."""

    async def test_relevant_entities_covers_main_and_subzaehler(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-wasser",
            bezeichnung="Wasser",
            kategorie="wasser",
            betragsmodus="verbrauch",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            verbrauchs_entity="sensor.wasser_gesamt",
            einheitspreis_eur=3.0,
            einheit="m3",
            verteilung="verbrauch",
            verbrauch_entities_pro_partei={
                "og": "sensor.wasser_og",
            },
        )
        entry = _make_entry(partei, kp)
        coord = await _make_coordinator(hass, entry)
        entities = coord._relevant_entities()
        assert entities == ["sensor.wasser_gesamt", "sensor.wasser_og"]

    async def test_relevant_entities_skips_none_values(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Positions without verbrauchs_entity contribute nothing to the set.

        Exercises the falsy-check branches in ``_relevant_entities``.
        """
        partei = _partei_subentry(subentry_id="og")
        kp_pauschal = _kp_subentry(
            subentry_id="kp-pauschal",
            bezeichnung="Versicherung",
            betrag_eur=450.0,
            periodizitaet="jaehrlich",
            verteilung="flaeche",
        )
        # Verbrauch kostenposition where the per-party dict has a None value.
        kp_partial = _kp_subentry(
            subentry_id="kp-partial",
            bezeichnung="Wasser",
            kategorie="wasser",
            betragsmodus="verbrauch",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            verbrauchs_entity="sensor.gesamt",
            einheitspreis_eur=3.0,
            einheit="m3",
            verteilung="verbrauch",
            verbrauch_entities_pro_partei={"og": ""},
        )
        entry = _make_entry(partei, kp_pauschal, kp_partial)
        coord = await _make_coordinator(hass, entry)
        entities = coord._relevant_entities()
        assert entities == ["sensor.gesamt"]


# ---------------------------------------------------------------------------
# Verbrauch edge cases (missing fields)
# ---------------------------------------------------------------------------


class TestVerbrauchMissingFields:
    """Missing verbrauch inputs surface as per-position errors."""

    async def test_missing_entity_id_on_verbrauch(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-w",
            bezeichnung="Wasser",
            kategorie="wasser",
            betragsmodus="verbrauch",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            verbrauchs_entity=None,
            einheitspreis_eur=3.0,
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        coord = await _make_coordinator(hass, entry)
        data = await coord._async_update_data()

        assert "verbrauchs_entity missing" in (
            data["parteien"]["og"]["positionen"][0]["error"] or ""
        )

    async def test_missing_einheitspreis_on_verbrauch(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-w",
            bezeichnung="Wasser",
            kategorie="wasser",
            betragsmodus="verbrauch",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            verbrauchs_entity="sensor.wasser",
            einheitspreis_eur=None,
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        hass.states.async_set("sensor.wasser", "10")
        coord = await _make_coordinator(hass, entry)
        data = await coord._async_update_data()

        assert "einheitspreis_eur missing" in (
            data["parteien"]["og"]["positionen"][0]["error"] or ""
        )


# ---------------------------------------------------------------------------
# Subentry normalisation edge cases
# ---------------------------------------------------------------------------


class TestSubentryNormalisation:
    """Normalisation helpers tolerate date objects and bad ISO strings."""

    async def test_date_object_passes_through(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A subentry that already carries a ``date`` object works (no parse)."""
        # Build the ConfigSubentry directly with a date instance so the
        # ``isinstance(value, date)`` branch in _parse_date is exercised.
        data: dict[str, Any] = {
            "name": "OG",
            "flaeche_qm": 85.0,
            "personen": 2,
            "bewohnt_ab": date(2020, 1, 1),
            "bewohnt_bis": None,
            "hinweis": None,
        }
        sub = ConfigSubentry(
            data=MappingProxyType(data),
            subentry_id="og",
            subentry_type=SUBENTRY_PARTEI,
            title="OG",
            unique_id=None,
        )
        entry = _make_entry(sub)
        coord = await _make_coordinator(hass, entry)
        data_out = await coord._async_update_data()
        assert "og" in data_out["parteien"]

    async def test_invalid_iso_date_falls_back_to_min(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A garbled ISO date is logged and becomes ``date.min`` so nothing crashes."""
        data: dict[str, Any] = {
            "name": "OG",
            "flaeche_qm": 85.0,
            "personen": 2,
            "bewohnt_ab": "not-a-date",
            "bewohnt_bis": None,
            "hinweis": None,
        }
        sub = ConfigSubentry(
            data=MappingProxyType(data),
            subentry_id="og",
            subentry_type=SUBENTRY_PARTEI,
            title="OG",
            unique_id=None,
        )
        entry = _make_entry(sub)
        coord = await _make_coordinator(hass, entry)
        with caplog.at_level("WARNING"):
            result = await coord._async_update_data()
        assert "og" in result["parteien"]
        assert any("Invalid ISO date" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Ad-hoc PARTEI allocation
# ---------------------------------------------------------------------------


class TestAdhocPartei:
    """Ad-hoc costs with zuordnung=PARTEI route fully to the target party."""

    async def test_adhoc_partei_direkt(
        self,
        hass: HomeAssistant,
    ) -> None:
        og = _partei_subentry(subentry_id="og")
        dg = _partei_subentry(
            subentry_id="dg",
            name="DG",
            flaeche_qm=65.0,
            bewohnt_ab=date(2019, 1, 1),
        )
        entry = _make_entry(og, dg)
        entry.add_to_hass(hass)
        store = HauskostenStore(hass, entry.entry_id)
        await store.async_load()
        await store.async_add_adhoc(
            {
                "id": "ah-partei",
                "bezeichnung": "Reparatur OG-Fenster",
                "kategorie": "sonstiges",
                "betrag_eur": 200.0,
                "datum": date(2026, 3, 1),
                "zuordnung": "partei",
                "zuordnung_partei_id": "og",
                "verteilung": "direkt",
                "bezahlt_am": None,
                "notiz": None,
            }
        )
        coord = HauskostenCoordinator(hass, entry, store)
        data = await coord._async_update_data()

        assert data["parteien"]["og"]["jahr_budget_eur"] == pytest.approx(200.0)
        assert data["parteien"]["dg"]["jahr_budget_eur"] == pytest.approx(0.0)

    async def test_adhoc_with_broken_allocation_marks_error(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An ad-hoc DIREKT with unknown target party produces an error."""
        og = _partei_subentry(subentry_id="og")
        entry = _make_entry(og)
        entry.add_to_hass(hass)
        store = HauskostenStore(hass, entry.entry_id)
        await store.async_load()
        await store.async_add_adhoc(
            {
                "id": "ah-bad",
                "bezeichnung": "Reparatur",
                "kategorie": "sonstiges",
                "betrag_eur": 50.0,
                "datum": date(2026, 3, 1),
                "zuordnung": "partei",
                "zuordnung_partei_id": "does-not-exist",
                "verteilung": "direkt",
                "bezahlt_am": None,
                "notiz": None,
            }
        )
        coord = HauskostenCoordinator(hass, entry, store)
        with caplog.at_level("WARNING"):
            data = await coord._async_update_data()
        errors = [
            p for p in data["parteien"]["og"]["positionen"] if p["error"] is not None
        ]
        assert len(errors) == 1


# ---------------------------------------------------------------------------
# Negative verbrauch state
# ---------------------------------------------------------------------------


class TestVerbrauchNegativeState:
    """Negative state values propagate via resolve_verbrauchs_betrag."""

    async def test_negative_state_is_flagged(
        self,
        hass: HomeAssistant,
    ) -> None:
        partei = _partei_subentry(subentry_id="og")
        kp = _kp_subentry(
            subentry_id="kp-w",
            bezeichnung="Wasser",
            kategorie="wasser",
            betragsmodus="verbrauch",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            verbrauchs_entity="sensor.wasser",
            einheitspreis_eur=3.0,
            verteilung="flaeche",
        )
        entry = _make_entry(partei, kp)
        hass.states.async_set("sensor.wasser", "-5")
        coord = await _make_coordinator(hass, entry)
        data = await coord._async_update_data()

        assert "verbrauch calc failed" in (
            data["parteien"]["og"]["positionen"][0]["error"] or ""
        )


# ---------------------------------------------------------------------------
# Subzaehler missing per-party entity id (dict entry None)
# ---------------------------------------------------------------------------


class TestSubzaehlerMissingId:
    """Subzaehler dict without an entry for a party surfaces an error."""

    async def test_no_entity_id_for_party(
        self,
        hass: HomeAssistant,
    ) -> None:
        og = _partei_subentry(subentry_id="og", flaeche_qm=85.0)
        dg = _partei_subentry(
            subentry_id="dg",
            name="DG",
            flaeche_qm=65.0,
            bewohnt_ab=date(2019, 1, 1),
        )
        kp = _kp_subentry(
            subentry_id="kp-wasser",
            bezeichnung="Wasser",
            kategorie="wasser",
            betragsmodus="verbrauch",
            betrag_eur=None,
            periodizitaet=None,
            faelligkeit=None,
            verbrauchs_entity="sensor.wasser_gesamt",
            einheitspreis_eur=3.0,
            einheit="m3",
            verteilung="verbrauch",
            verbrauch_entities_pro_partei={"og": "sensor.wasser_og"},
        )
        entry = _make_entry(og, dg, kp)
        hass.states.async_set("sensor.wasser_gesamt", "100")
        hass.states.async_set("sensor.wasser_og", "70")
        coord = await _make_coordinator(hass, entry)
        data = await coord._async_update_data()
        # dg has no per-party entity -> error on the position.
        assert "subzaehler entity missing" in (
            data["parteien"]["og"]["positionen"][0]["error"] or ""
        )
