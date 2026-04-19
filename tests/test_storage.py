"""Tests for custom_components.hauskosten.storage.

Authoritative oracles:
* docs/ARCHITECTURE.md -- Storage Layer section.
* docs/DATA_MODEL.md   -- AdHocKosten schema and schema-versioning rules.
* AGENTS.md hard constraint #3: file I/O only via the Storage API, never in
  the coordinator update callback.

The tests exercise the HauskostenStore wrapper around
``homeassistant.helpers.storage.Store``. They rely on
``pytest-homeassistant-custom-component`` for the ``hass`` and ``hass_storage``
fixtures.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.hauskosten.const import DOMAIN, STORAGE_VERSION
from custom_components.hauskosten.models import Kategorie, Verteilung, Zuordnung
from custom_components.hauskosten.storage import HauskostenStore

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


ENTRY_ID = "test-entry-1234"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _adhoc(
    *,
    kid: str = "ah-1",
    bezeichnung: str = "Handwerker",
    betrag_eur: float = 150.0,
    datum: date = date(2026, 3, 15),
    kategorie: Kategorie = Kategorie.SONSTIGES,
    zuordnung: Zuordnung = Zuordnung.HAUS,
    zuordnung_partei_id: str | None = None,
    verteilung: Verteilung = Verteilung.GLEICH,
    bezahlt_am: date | None = None,
    notiz: str | None = None,
) -> dict[str, Any]:
    """Return an AdHocKosten-shaped dict."""
    return {
        "id": kid,
        "bezeichnung": bezeichnung,
        "kategorie": kategorie,
        "betrag_eur": betrag_eur,
        "datum": datum,
        "zuordnung": zuordnung,
        "zuordnung_partei_id": zuordnung_partei_id,
        "verteilung": verteilung,
        "bezahlt_am": bezahlt_am,
        "notiz": notiz,
    }


def _storage_key(entry_id: str = ENTRY_ID) -> str:
    return f"{DOMAIN}.{entry_id}"


# ---------------------------------------------------------------------------
# Construction & initial load
# ---------------------------------------------------------------------------


class TestHauskostenStoreConstruction:
    """Construction semantics and defaults."""

    async def test_key_pattern_uses_domain_and_entry_id(
        self,
        hass: HomeAssistant,
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        assert store.key == _storage_key()

    async def test_version_matches_constant(self, hass: HomeAssistant) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        assert store.version == STORAGE_VERSION

    async def test_load_on_missing_file_yields_empty_state(
        self,
        hass: HomeAssistant,
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        assert store.adhoc_kosten == []
        assert store.paid_records == {}

    async def test_load_is_idempotent(self, hass: HomeAssistant) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        await store.async_load()
        assert store.adhoc_kosten == []


# ---------------------------------------------------------------------------
# Ad-hoc add / remove
# ---------------------------------------------------------------------------


class TestAdHocKosten:
    """CRUD on the ad-hoc cost list."""

    async def test_add_adhoc_persists(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        item = _adhoc()
        await store.async_add_adhoc(item)

        assert store.adhoc_kosten == [item]
        # Verify the data actually hit the in-memory storage mock.
        saved = hass_storage[_storage_key()]
        assert saved["version"] == STORAGE_VERSION
        assert saved["data"]["ad_hoc_kosten"][0]["id"] == "ah-1"

    async def test_add_multiple_preserves_order(
        self,
        hass: HomeAssistant,
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        a = _adhoc(kid="ah-a")
        b = _adhoc(kid="ah-b")
        c = _adhoc(kid="ah-c")
        await store.async_add_adhoc(a)
        await store.async_add_adhoc(b)
        await store.async_add_adhoc(c)
        assert [i["id"] for i in store.adhoc_kosten] == ["ah-a", "ah-b", "ah-c"]

    async def test_add_duplicate_id_raises(
        self,
        hass: HomeAssistant,
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        await store.async_add_adhoc(_adhoc(kid="dup"))
        with pytest.raises(ValueError, match="duplicate"):
            await store.async_add_adhoc(_adhoc(kid="dup"))

    async def test_remove_existing_adhoc(
        self,
        hass: HomeAssistant,
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        await store.async_add_adhoc(_adhoc(kid="ah-a"))
        await store.async_add_adhoc(_adhoc(kid="ah-b"))
        await store.async_remove_adhoc("ah-a")
        assert [i["id"] for i in store.adhoc_kosten] == ["ah-b"]

    async def test_remove_adhoc_skips_earlier_mismatches(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Removing a non-first entry exercises the loop-continue branch."""
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        await store.async_add_adhoc(_adhoc(kid="ah-a"))
        await store.async_add_adhoc(_adhoc(kid="ah-b"))
        await store.async_add_adhoc(_adhoc(kid="ah-c"))
        await store.async_remove_adhoc("ah-b")
        assert [i["id"] for i in store.adhoc_kosten] == ["ah-a", "ah-c"]

    async def test_remove_unknown_id_raises(
        self,
        hass: HomeAssistant,
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        with pytest.raises(KeyError, match="ah-missing"):
            await store.async_remove_adhoc("ah-missing")

    async def test_adhoc_kosten_property_returns_copy(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Mutating the returned list must not affect internal state."""
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        await store.async_add_adhoc(_adhoc(kid="ah-a"))
        snapshot = store.adhoc_kosten
        snapshot.clear()
        assert len(store.adhoc_kosten) == 1


# ---------------------------------------------------------------------------
# Mark paid
# ---------------------------------------------------------------------------


class TestMarkPaid:
    """Payment-history semantics."""

    async def test_mark_paid_stores_date(
        self,
        hass: HomeAssistant,
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        await store.async_mark_paid("kp-1", date(2026, 3, 15))
        assert store.paid_records == {"kp-1": date(2026, 3, 15)}

    async def test_mark_paid_idempotent_overwrites(
        self,
        hass: HomeAssistant,
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        await store.async_mark_paid("kp-1", date(2026, 3, 15))
        await store.async_mark_paid("kp-1", date(2026, 4, 1))
        assert store.paid_records == {"kp-1": date(2026, 4, 1)}

    async def test_paid_records_property_returns_copy(
        self,
        hass: HomeAssistant,
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        await store.async_mark_paid("kp-1", date(2026, 3, 15))
        snapshot = store.paid_records
        snapshot.clear()
        assert store.paid_records == {"kp-1": date(2026, 3, 15)}


# ---------------------------------------------------------------------------
# Serialisation round-trip
# ---------------------------------------------------------------------------


class TestSerialisation:
    """JSON round-trip with date fields."""

    async def test_date_round_trip_adhoc(
        self,
        hass: HomeAssistant,
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        item = _adhoc(
            kid="ah-1",
            datum=date(2026, 3, 15),
            bezahlt_am=date(2026, 4, 1),
        )
        await store.async_add_adhoc(item)

        # Re-load in a fresh store instance; the on-disk JSON representation
        # must deserialise back to datetime.date objects.
        store2 = HauskostenStore(hass, ENTRY_ID)
        await store2.async_load()
        loaded = store2.adhoc_kosten[0]
        assert loaded["datum"] == date(2026, 3, 15)
        assert loaded["bezahlt_am"] == date(2026, 4, 1)
        assert loaded["id"] == "ah-1"

    async def test_date_round_trip_paid_records(
        self,
        hass: HomeAssistant,
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        await store.async_mark_paid("kp-1", date(2026, 3, 15))

        store2 = HauskostenStore(hass, ENTRY_ID)
        await store2.async_load()
        assert store2.paid_records == {"kp-1": date(2026, 3, 15)}

    async def test_none_bezahlt_am_round_trips(
        self,
        hass: HomeAssistant,
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        await store.async_add_adhoc(_adhoc(kid="ah-1", bezahlt_am=None))

        store2 = HauskostenStore(hass, ENTRY_ID)
        await store2.async_load()
        assert store2.adhoc_kosten[0]["bezahlt_am"] is None

    async def test_explicit_save_persists_current_state(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        await store.async_add_adhoc(_adhoc(kid="ah-1"))
        await store.async_save()
        assert hass_storage[_storage_key()]["data"]["ad_hoc_kosten"][0]["id"] == "ah-1"


# ---------------------------------------------------------------------------
# Per-entry isolation
# ---------------------------------------------------------------------------


class TestPerEntryIsolation:
    """Two entries must not share state."""

    async def test_different_entries_use_separate_files(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
    ) -> None:
        store_a = HauskostenStore(hass, "entry-a")
        store_b = HauskostenStore(hass, "entry-b")
        await store_a.async_load()
        await store_b.async_load()
        await store_a.async_add_adhoc(_adhoc(kid="ah-a"))
        await store_b.async_add_adhoc(_adhoc(kid="ah-b"))

        assert [i["id"] for i in store_a.adhoc_kosten] == ["ah-a"]
        assert [i["id"] for i in store_b.adhoc_kosten] == ["ah-b"]
        assert f"{DOMAIN}.entry-a" in hass_storage
        assert f"{DOMAIN}.entry-b" in hass_storage


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


class TestSchemaMigration:
    """Ensure the migration hook runs for older versions."""

    async def test_migrate_from_v0_returns_empty_lists(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
    ) -> None:
        """A pre-v1 payload without the expected keys must not crash."""
        hass_storage[_storage_key()] = {
            "version": 0,
            "minor_version": 1,
            "key": _storage_key(),
            "data": {"legacy_field": "ignored"},
        }
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        assert store.adhoc_kosten == []
        assert store.paid_records == {}

    async def test_migrate_preserves_compatible_fields(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
    ) -> None:
        """A v0 payload that happens to contain ad_hoc_kosten survives."""
        hass_storage[_storage_key()] = {
            "version": 0,
            "minor_version": 1,
            "key": _storage_key(),
            "data": {
                "ad_hoc_kosten": [
                    {
                        "id": "legacy-1",
                        "bezeichnung": "Alt",
                        "kategorie": "sonstiges",
                        "betrag_eur": 10.0,
                        "datum": "2025-01-01",
                        "zuordnung": "haus",
                        "zuordnung_partei_id": None,
                        "verteilung": "gleich",
                        "bezahlt_am": None,
                        "notiz": None,
                    },
                ],
                "paid_records": {"kp-legacy": "2025-06-01"},
            },
        }
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        assert len(store.adhoc_kosten) == 1
        assert store.adhoc_kosten[0]["id"] == "legacy-1"
        assert store.adhoc_kosten[0]["datum"] == date(2025, 1, 1)
        assert store.paid_records == {"kp-legacy": date(2025, 6, 1)}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Corner cases that must not blow up."""

    async def test_empty_store_save_then_load(
        self,
        hass: HomeAssistant,
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        await store.async_save()  # no data yet, must not raise

        store2 = HauskostenStore(hass, ENTRY_ID)
        await store2.async_load()
        assert store2.adhoc_kosten == []
        assert store2.paid_records == {}

    async def test_add_adhoc_without_load_autoloads(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Operations before an explicit load must still work (autoload)."""
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_add_adhoc(_adhoc(kid="ah-1"))
        assert [i["id"] for i in store.adhoc_kosten] == ["ah-1"]

    async def test_mark_paid_without_load_autoloads(
        self,
        hass: HomeAssistant,
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_mark_paid("kp-1", date(2026, 3, 15))
        assert store.paid_records == {"kp-1": date(2026, 3, 15)}

    async def test_adhoc_kosten_getter_before_load_returns_empty(
        self,
        hass: HomeAssistant,
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        assert store.adhoc_kosten == []
        assert store.paid_records == {}

    async def test_corrupt_data_shape_recovers_to_empty(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
    ) -> None:
        """A stored dict missing the expected keys must load as empty."""
        hass_storage[_storage_key()] = {
            "version": STORAGE_VERSION,
            "minor_version": 1,
            "key": _storage_key(),
            "data": {"something_else": True},
        }
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        assert store.adhoc_kosten == []
        assert store.paid_records == {}

    async def test_invalid_iso_date_in_adhoc_falls_back_to_string(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
    ) -> None:
        """Malformed ISO date strings in an ad-hoc record log a warning."""
        hass_storage[_storage_key()] = {
            "version": STORAGE_VERSION,
            "minor_version": 1,
            "key": _storage_key(),
            "data": {
                "ad_hoc_kosten": [
                    {
                        "id": "ah-bad",
                        "bezeichnung": "Kaputt",
                        "kategorie": "sonstiges",
                        "betrag_eur": 1.0,
                        "datum": "not-a-date",
                        "zuordnung": "haus",
                        "zuordnung_partei_id": None,
                        "verteilung": "gleich",
                        "bezahlt_am": None,
                        "notiz": None,
                    },
                ],
                "paid_records": {},
            },
        }
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        # The ad-hoc entry is still loaded but the bad date is left as the
        # raw string so the caller can see what went wrong.
        assert store.adhoc_kosten[0]["datum"] == "not-a-date"

    async def test_invalid_iso_date_in_paid_records_is_dropped(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
    ) -> None:
        """Malformed ISO date strings in paid_records are skipped."""
        hass_storage[_storage_key()] = {
            "version": STORAGE_VERSION,
            "minor_version": 1,
            "key": _storage_key(),
            "data": {
                "ad_hoc_kosten": [],
                "paid_records": {
                    "kp-good": "2026-03-15",
                    "kp-bad": "garbage",
                },
            },
        }
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        assert store.paid_records == {"kp-good": date(2026, 3, 15)}

    async def test_non_string_date_in_paid_records_is_dropped(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
    ) -> None:
        """Unexpected types in paid_records are logged and skipped."""
        hass_storage[_storage_key()] = {
            "version": STORAGE_VERSION,
            "minor_version": 1,
            "key": _storage_key(),
            "data": {
                "ad_hoc_kosten": [],
                "paid_records": {"kp-weird": 12345},
            },
        }
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        assert store.paid_records == {}

    async def test_non_dict_entries_in_adhoc_list_are_skipped(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
    ) -> None:
        """Only dict-shaped entries survive; malformed entries drop."""
        hass_storage[_storage_key()] = {
            "version": STORAGE_VERSION,
            "minor_version": 1,
            "key": _storage_key(),
            "data": {
                "ad_hoc_kosten": [
                    "not-a-dict",
                    {
                        "id": "ah-ok",
                        "bezeichnung": "Gut",
                        "kategorie": "sonstiges",
                        "betrag_eur": 1.0,
                        "datum": "2026-01-01",
                        "zuordnung": "haus",
                        "zuordnung_partei_id": None,
                        "verteilung": "gleich",
                        "bezahlt_am": None,
                        "notiz": None,
                    },
                ],
                "paid_records": {},
            },
        }
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()
        assert [i["id"] for i in store.adhoc_kosten] == ["ah-ok"]

    async def test_date_object_in_paid_records_passes_through(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Date objects (already hydrated) survive the parse helper."""
        # Exercises the ``isinstance(raw, date)`` short-circuit in
        # HauskostenStore._parse_date; happens if an upstream caller writes
        # a date directly into the in-memory cache before save / reload.
        store = HauskostenStore(hass, ENTRY_ID)
        # Inject a date into the raw payload via the internal helper.
        parsed = HauskostenStore._parse_date(date(2026, 5, 5), context="injected")
        assert parsed == date(2026, 5, 5)
        del store  # silence unused-local linter


class TestIOErrorPaths:
    """Verify the load/save exception branches log and re-raise."""

    async def test_load_error_is_logged_and_reraised(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)

        async def boom() -> None:
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(store._store, "async_load", boom)
        with caplog.at_level("ERROR"), pytest.raises(RuntimeError, match="disk"):
            await store.async_load()
        assert "Failed to load hauskosten store" in caplog.text

    async def test_save_error_is_logged_and_reraised(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        store = HauskostenStore(hass, ENTRY_ID)
        await store.async_load()

        async def boom(_payload: Any) -> None:
            raise OSError("no space left")

        monkeypatch.setattr(store._store, "async_save", boom)
        with caplog.at_level("ERROR"), pytest.raises(OSError, match="no space"):
            await store.async_save()
        assert "Failed to save hauskosten store" in caplog.text
