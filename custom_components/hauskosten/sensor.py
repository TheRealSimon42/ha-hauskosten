"""Sensor platform for the hauskosten integration.

Builds a dynamic fleet of sensors driven by :class:`HauskostenCoordinator`.
Per config entry we create:

* **Per partei** -- ``monat_aktuell``, ``jahr_aktuell``, ``jahr_budget``,
  ``naechste_faelligkeit`` plus one ``kategorie_jahr`` sensor per cost
  category the party actually has with a non-zero share.
* **House-wide** -- ``jahr_gesamt``, ``jahr_budget``, one ``kategorie_jahr``
  sensor per category with a non-zero share, and a single
  ``naechste_faelligkeit`` across all parties.

All entities inherit from :class:`HauskostenSensorBase`, which wires the
:class:`CoordinatorEntity` glue, a common :class:`DeviceInfo` (one device
per config entry), ``_attr_has_entity_name = True`` and a stable
``unique_id`` schema ``{entry_id}_{scope}_{subject?}_{zweck}`` (never based
on the party name -- see Phase 1.3 lessons).

Entities are created dynamically: at setup time from the initial
``coordinator.data`` and then again whenever a coordinator refresh reveals a
new party or category. The :meth:`HauskostenCoordinator.async_add_listener`
callback is our re-scan hook, guarded by a set of known unique-ids so we
never double-register.

Authoritative specs:

* ``docs/ARCHITECTURE.md`` -- "5. Sensor-Platform" + Entity-Naming-Konvention.
* ``docs/STANDARDS.md``    -- "Entity-Design": translation keys,
  device / state classes, unique-id stability, properties without logic.
* ``AGENTS.md`` hard constraint #2 (only sensors) and #7 (translations only
  for user-visible strings).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import CURRENCY_EURO
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SUBENTRY_KOSTENPOSITION
from .models import Betragsmodus, Kategorie

if TYPE_CHECKING:
    from datetime import date

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import HauskostenCoordinator
    from .models import CoordinatorData, ParteiResult, PositionAttribution

__all__ = ["async_setup_entry"]

_LOGGER = logging.getLogger(__name__)

# Translation keys for sensors (matching strings.json entity.sensor.*).
_TRKEY_PARTEI_MONAT = "partei_monat_aktuell"
_TRKEY_PARTEI_JAHR_AKTUELL = "partei_jahr_aktuell"
_TRKEY_PARTEI_JAHR_BUDGET = "partei_jahr_budget"
_TRKEY_PARTEI_FAELLIG = "partei_naechste_faelligkeit"
_TRKEY_PARTEI_KAT = "partei_kategorie_jahr"
_TRKEY_PARTEI_ABSCHLAG_GEZAHLT = "partei_abschlag_gezahlt_jahr"
_TRKEY_PARTEI_ABSCHLAG_IST = "partei_abschlag_ist_jahr"
_TRKEY_PARTEI_ABSCHLAG_SALDO = "partei_abschlag_saldo_jahr"
_TRKEY_HAUS_GESAMT = "haus_jahr_gesamt"
_TRKEY_HAUS_BUDGET = "haus_jahr_budget"
_TRKEY_HAUS_KAT = "haus_kategorie_jahr"
_TRKEY_HAUS_FAELLIG = "naechste_faelligkeit"
_TRKEY_HAUS_ABSCHLAG_GEZAHLT = "haus_abschlag_gezahlt_jahr"
_TRKEY_HAUS_ABSCHLAG_IST = "haus_abschlag_ist_jahr"
_TRKEY_HAUS_ABSCHLAG_SALDO = "haus_abschlag_saldo_jahr"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for a config entry.

    Builds the initial fleet of sensors from the coordinator's first snapshot
    and registers a listener that catches any new party / category revealed
    by subsequent refreshes. The listener tracks known unique-ids so it only
    adds genuinely new entities.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being set up.
        async_add_entities: Callback for registering entities with HA.
    """
    coordinator: HauskostenCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    known_ids: set[str] = set()

    initial = _build_sensors(coordinator, entry, known_ids)
    if initial:
        async_add_entities(initial)
    _LOGGER.debug(
        "Registered %d initial hauskosten sensors for entry %s",
        len(initial),
        entry.entry_id,
    )

    @callback
    def _rescan() -> None:
        """Re-scan coordinator.data for sensors we have not created yet."""
        new_entities = _build_sensors(coordinator, entry, known_ids)
        if new_entities:
            _LOGGER.debug(
                "Dynamically adding %d hauskosten sensors for entry %s",
                len(new_entities),
                entry.entry_id,
            )
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_rescan))


# ---------------------------------------------------------------------------
# Entity factory
# ---------------------------------------------------------------------------


def _build_sensors(
    coordinator: HauskostenCoordinator,
    entry: ConfigEntry,
    known_ids: set[str],
) -> list[HauskostenSensorBase]:
    """Produce the list of sensors that need registering right now.

    Examines the current ``coordinator.data`` snapshot, compares each
    candidate's ``unique_id`` against the ``known_ids`` set and returns the
    newly-needed entities. Mutates ``known_ids`` so future scans skip them.

    Args:
        coordinator: The active :class:`HauskostenCoordinator` (may carry
            ``None`` data before the first refresh -- we then return [] ).
        entry: The config entry the entities will be attached to.
        known_ids: Set of unique-ids already registered. Mutated in place.

    Returns:
        List of freshly-instantiated sensor entities ready for
        ``async_add_entities``.
    """
    data: CoordinatorData | None = coordinator.data
    if data is None:  # pragma: no cover - first refresh populates it
        return []

    entry_id = entry.entry_id
    new: list[HauskostenSensorBase] = []
    abschlag_positions: list[tuple[str, str]] = _abschlag_positions(entry)

    for partei_id, partei_result in data["parteien"].items():
        new.extend(
            _build_partei_sensors(
                coordinator,
                entry_id,
                partei_id,
                partei_result,
                abschlag_positions,
                known_ids,
            )
        )
    new.extend(
        _build_haus_sensors(coordinator, entry_id, data, abschlag_positions, known_ids)
    )
    return new


def _build_partei_sensors(
    coordinator: HauskostenCoordinator,
    entry_id: str,
    partei_id: str,
    partei_result: ParteiResult,
    abschlag_positions: list[tuple[str, str]],
    known_ids: set[str],
) -> list[HauskostenSensorBase]:
    """Return the set of party-scoped sensors that still need registering."""
    new: list[HauskostenSensorBase] = []
    for partei_cls in _PARTEI_SENSOR_CLASSES:
        uid = partei_cls.make_unique_id(entry_id, partei_id)
        if uid in known_ids:
            continue
        known_ids.add(uid)
        new.append(partei_cls(coordinator, entry_id, partei_id))
    for kategorie in partei_result["pro_kategorie_jahr_eur"]:
        uid = ParteiKategorieSensor.make_unique_id(entry_id, partei_id, kategorie)
        if uid in known_ids:
            continue
        known_ids.add(uid)
        new.append(ParteiKategorieSensor(coordinator, entry_id, partei_id, kategorie))
    for kp_id, bezeichnung in abschlag_positions:
        for abschlag_cls in _PARTEI_ABSCHLAG_SENSOR_CLASSES:
            uid = abschlag_cls.make_unique_id(entry_id, partei_id, kp_id)
            if uid in known_ids:
                continue
            known_ids.add(uid)
            new.append(
                abschlag_cls(coordinator, entry_id, partei_id, kp_id, bezeichnung)
            )
    return new


def _build_haus_sensors(
    coordinator: HauskostenCoordinator,
    entry_id: str,
    data: CoordinatorData,
    abschlag_positions: list[tuple[str, str]],
    known_ids: set[str],
) -> list[HauskostenSensorBase]:
    """Return the set of house-scoped sensors that still need registering."""
    new: list[HauskostenSensorBase] = []
    for haus_cls in _HAUS_SENSOR_CLASSES:
        uid = haus_cls.make_haus_unique_id(entry_id)
        if uid in known_ids:
            continue
        known_ids.add(uid)
        new.append(haus_cls(coordinator, entry_id))
    for kategorie in data["haus"]["pro_kategorie_jahr_eur"]:
        uid = HausKategorieSensor.make_unique_id(entry_id, kategorie)
        if uid in known_ids:
            continue
        known_ids.add(uid)
        new.append(HausKategorieSensor(coordinator, entry_id, kategorie))
    for kp_id, bezeichnung in abschlag_positions:
        for haus_abschlag_cls in _HAUS_ABSCHLAG_SENSOR_CLASSES:
            uid = haus_abschlag_cls.make_abschlag_unique_id(entry_id, kp_id)
            if uid in known_ids:
                continue
            known_ids.add(uid)
            new.append(haus_abschlag_cls(coordinator, entry_id, kp_id, bezeichnung))
    return new


def _abschlag_positions(entry: ConfigEntry) -> list[tuple[str, str]]:
    """Return ``(kp_id, bezeichnung)`` for every ABSCHLAG subentry of ``entry``.

    Reads straight from ``entry.subentries`` instead of probing the
    coordinator snapshot so we also emit sensors for mis-configured
    positions (e.g. missing anchor) whose attributions carry ``None``
    values -- the user sees "unavailable" rather than missing entities.
    """
    result: list[tuple[str, str]] = []
    for sub in entry.subentries.values():
        if sub.subentry_type != SUBENTRY_KOSTENPOSITION:
            continue
        if sub.data.get("betragsmodus") != Betragsmodus.ABSCHLAG.value:
            continue
        result.append((sub.subentry_id, str(sub.data.get("bezeichnung", ""))))
    return result


# ---------------------------------------------------------------------------
# Base classes
# ---------------------------------------------------------------------------


class HauskostenSensorBase(CoordinatorEntity["HauskostenCoordinator"], SensorEntity):
    """Shared base for every hauskosten sensor.

    Wires :class:`CoordinatorEntity` so HA schedules an update on every
    coordinator refresh, establishes a single :class:`DeviceInfo` per
    config entry (so all sensors group under one device in the UI) and
    enforces ``_attr_has_entity_name = True`` plus an absent ``_attr_name``
    so the UI falls back to the translated ``_attr_translation_key``.

    Subclasses provide:

    * ``_attr_translation_key`` -- the entity.sensor.<key> in strings.json.
    * ``_attr_device_class`` / ``_attr_state_class`` / unit as applicable.
    * ``make_unique_id`` / ``make_haus_unique_id`` class methods, because
      the factory needs the unique-id *before* constructing the entity so
      the ``known_ids`` set can deduplicate.
    * ``native_value`` / ``extra_state_attributes`` -- read-only accessors
      into ``coordinator.data``, never doing any math.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: HauskostenCoordinator, entry_id: str) -> None:
        """Initialise the shared CoordinatorEntity + DeviceInfo plumbing.

        Args:
            coordinator: The active :class:`HauskostenCoordinator`.
            entry_id: The config entry id, used for device identifiers and
                as the first segment of every ``unique_id``.
        """
        super().__init__(coordinator)
        self._entry_id = entry_id
        entry = coordinator.config_entry
        title = entry.title if entry is not None else "Hauskosten"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=title,
            manufacturer="simon42",
            model="Hauskosten",
        )


class ParteiSensorBase(HauskostenSensorBase):
    """Shared base for party-scoped sensors.

    Populates the ``partei`` and ``jahr`` translation placeholders so the
    strings in ``strings.json`` (``{partei} - Kosten {jahr}`` etc.) are
    substituted at render time. Subclasses that need additional
    placeholders (e.g. ``kategorie``) extend
    ``_build_translation_placeholders``.
    """

    _scope: ClassVar[str] = "partei"
    _zweck: ClassVar[str]

    def __init__(
        self,
        coordinator: HauskostenCoordinator,
        entry_id: str,
        partei_id: str,
    ) -> None:
        """Store the target party id and build the unique-id."""
        super().__init__(coordinator, entry_id)
        self._partei_id = partei_id
        self._attr_unique_id = self.make_unique_id(entry_id, partei_id)
        self._attr_translation_placeholders = self._build_translation_placeholders()

    def _build_translation_placeholders(self) -> dict[str, str]:
        """Return the translation placeholders for this entity's name template.

        Party id and the current year are the minimum a party-scoped sensor
        needs; subclasses add their own via ``super()`` + additions.
        """
        data = self.coordinator.data
        partei_name = self._partei_id
        jahr = ""
        if data is not None:
            result = data["parteien"].get(self._partei_id)
            if result is not None:
                partei_name = result["partei"]["name"]
            jahr = str(data["jahr"])
        return {"partei": partei_name, "jahr": jahr}

    @classmethod
    def make_unique_id(cls, entry_id: str, partei_id: str) -> str:
        """Return the stable unique-id for this class and party."""
        return f"{entry_id}_{cls._scope}_{partei_id}_{cls._zweck}"

    def _partei_result(self) -> ParteiResult | None:
        """Return the ParteiResult for this party, or ``None`` if it vanished.

        ``CoordinatorEntity`` types ``coordinator.data`` as the coordinator's
        data generic (non-optional) once HA has populated it; before the first
        refresh HA never dispatches to entities, so we can rely on the data
        being present here.
        """
        return self.coordinator.data["parteien"].get(self._partei_id)

    @property
    def available(self) -> bool:
        """Return True when the party is still present in the snapshot."""
        return super().available and self._partei_result() is not None


class HausSensorBase(HauskostenSensorBase):
    """Shared base for house-wide sensors."""

    _zweck: ClassVar[str]

    def __init__(self, coordinator: HauskostenCoordinator, entry_id: str) -> None:
        """Store entry id and build the haus-scoped unique-id."""
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = self.make_haus_unique_id(entry_id)
        self._attr_translation_placeholders = self._build_translation_placeholders()

    def _build_translation_placeholders(self) -> dict[str, str]:
        """Return translation placeholders for house-wide sensor names."""
        data = self.coordinator.data
        jahr = str(data["jahr"]) if data is not None else ""
        return {"jahr": jahr}

    @classmethod
    def make_haus_unique_id(cls, entry_id: str) -> str:
        """Return the stable unique-id for this house-wide sensor."""
        return f"{entry_id}_haus_{cls._zweck}"


# ---------------------------------------------------------------------------
# Per-party sensor concrete classes
# ---------------------------------------------------------------------------


class _EuroPartyMixin(SensorEntity):
    """Shared attributes for EUR-valued party sensors.

    Inherits from :class:`SensorEntity` so mypy sees the attribute overrides
    as narrowings of the base's annotations rather than conflicts.
    """

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = CURRENCY_EURO
    _attr_suggested_display_precision = 2


class ParteiMonatSensor(_EuroPartyMixin, ParteiSensorBase):
    """Costs of the current month for one party."""

    _zweck = "monat_aktuell"
    _attr_translation_key = _TRKEY_PARTEI_MONAT
    _attr_icon = "mdi:calendar-month"

    @property
    def native_value(self) -> float | None:
        """Return the month-to-date EUR total from the coordinator."""
        result = self._partei_result()
        if result is None:
            return None
        return result["monat_aktuell_eur"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return structured attributes for dashboard drill-down."""
        return _partei_attrs(self._partei_result(), self.coordinator.data)


class ParteiJahrAktuellSensor(_EuroPartyMixin, ParteiSensorBase):
    """Year-to-date cost total for one party."""

    _zweck = "jahr_aktuell"
    _attr_translation_key = _TRKEY_PARTEI_JAHR_AKTUELL
    _attr_icon = "mdi:cash"

    @property
    def native_value(self) -> float | None:
        """Return the year-to-date EUR total from the coordinator."""
        result = self._partei_result()
        if result is None:
            return None
        return result["jahr_aktuell_eur"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return structured attributes including the list of positionen."""
        return _partei_attrs(
            self._partei_result(),
            self.coordinator.data,
            include_positionen=True,
        )


class ParteiJahrBudgetSensor(_EuroPartyMixin, ParteiSensorBase):
    """Projected full-year budget for one party."""

    _zweck = "jahr_budget"
    _attr_translation_key = _TRKEY_PARTEI_JAHR_BUDGET
    _attr_icon = "mdi:calendar-end"

    @property
    def native_value(self) -> float | None:
        """Return the projected annual budget (EUR) for this party."""
        result = self._partei_result()
        if result is None:
            return None
        return result["jahr_budget_eur"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return structured attributes for dashboard drill-down."""
        return _partei_attrs(self._partei_result(), self.coordinator.data)


class ParteiNaechsteFaelligkeitSensor(ParteiSensorBase):
    """Earliest upcoming due date for one party."""

    _zweck = "naechste_faelligkeit"
    _attr_translation_key = _TRKEY_PARTEI_FAELLIG
    _attr_device_class = SensorDeviceClass.DATE
    _attr_icon = "mdi:calendar-clock"

    @property
    def native_value(self) -> date | None:
        """Return the next due date for this party's positions."""
        result = self._partei_result()
        if result is None:
            return None
        return result["naechste_faelligkeit"]


class ParteiKategorieSensor(_EuroPartyMixin, ParteiSensorBase):
    """Per-category yearly cost for one party (dynamic one per category)."""

    _zweck = "kategorie"  # unused: unique_id built from category value
    _attr_translation_key = _TRKEY_PARTEI_KAT

    def __init__(
        self,
        coordinator: HauskostenCoordinator,
        entry_id: str,
        partei_id: str,
        kategorie: Kategorie,
    ) -> None:
        """Store the target category and build a category-aware unique-id."""
        self._kategorie = kategorie
        # super() populates translation placeholders via
        # ``_build_translation_placeholders`` which we override below.
        super().__init__(coordinator, entry_id, partei_id)
        self._attr_unique_id = self.make_unique_id(entry_id, partei_id, kategorie)

    def _build_translation_placeholders(self) -> dict[str, str]:
        """Extend the party placeholders with the ``kategorie`` value."""
        placeholders = super()._build_translation_placeholders()
        placeholders["kategorie"] = self._kategorie.value
        return placeholders

    @classmethod
    def make_unique_id(
        cls,
        entry_id: str,
        partei_id: str,
        kategorie: Kategorie | None = None,
    ) -> str:
        """Return the stable unique-id including the category segment."""
        if kategorie is None:
            # Guardrail for callers that use the non-category base signature.
            return f"{entry_id}_partei_{partei_id}_kategorie"
        return f"{entry_id}_partei_{partei_id}_kategorie_{kategorie.value}_jahr"

    @property
    def native_value(self) -> float | None:
        """Return this party's yearly share for the target category."""
        result = self._partei_result()
        if result is None:
            return None
        return result["pro_kategorie_jahr_eur"].get(self._kategorie)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the list of positionen filtered to this category."""
        result = self._partei_result()
        base = _partei_attrs(result, self.coordinator.data, include_positionen=False)
        base["kategorie"] = self._kategorie.value
        if result is not None:
            base["positionen"] = [
                _position_attrs(p)
                for p in result["positionen"]
                if p["kategorie"] == self._kategorie
            ]
        return base


# ---------------------------------------------------------------------------
# Haus-level sensor concrete classes
# ---------------------------------------------------------------------------


class _EuroHausMixin(SensorEntity):
    """Shared attributes for EUR-valued house-wide sensors.

    Inherits from :class:`SensorEntity` so mypy sees the attribute overrides
    as narrowings of the base's annotations rather than conflicts.
    """

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = CURRENCY_EURO
    _attr_suggested_display_precision = 2


class HausJahrGesamtSensor(_EuroHausMixin, HausSensorBase):
    """Year-to-date total for the whole house."""

    _zweck = "jahr_gesamt"
    _attr_translation_key = _TRKEY_HAUS_GESAMT
    _attr_icon = "mdi:home-outline"

    @property
    def native_value(self) -> float | None:
        """Return the house-wide year-to-date total (EUR)."""
        return self.coordinator.data["haus"]["jahr_aktuell_eur"]


class HausJahrBudgetSensor(_EuroHausMixin, HausSensorBase):
    """Projected full-year budget for the whole house."""

    _zweck = "jahr_budget"
    _attr_translation_key = _TRKEY_HAUS_BUDGET
    _attr_icon = "mdi:home-currency-usd"

    @property
    def native_value(self) -> float | None:
        """Return the house-wide yearly budget (EUR)."""
        return self.coordinator.data["haus"]["jahr_budget_eur"]


class HausNaechsteFaelligkeitSensor(HausSensorBase):
    """Earliest upcoming due date across all parties."""

    _zweck = "naechste_faelligkeit"
    _attr_translation_key = _TRKEY_HAUS_FAELLIG
    _attr_device_class = SensorDeviceClass.DATE
    _attr_icon = "mdi:calendar-star"

    @property
    def native_value(self) -> date | None:
        """Return the earliest due date across all parties, or ``None``."""
        dates = [
            p["naechste_faelligkeit"]
            for p in self.coordinator.data["parteien"].values()
            if p["naechste_faelligkeit"] is not None
        ]
        if not dates:
            return None
        return min(dates)


class HausKategorieSensor(_EuroHausMixin, HausSensorBase):
    """Per-category yearly total across the whole house."""

    _zweck = "kategorie"
    _attr_translation_key = _TRKEY_HAUS_KAT

    def __init__(
        self,
        coordinator: HauskostenCoordinator,
        entry_id: str,
        kategorie: Kategorie,
    ) -> None:
        """Store the target category and build a category-aware unique-id."""
        self._kategorie = kategorie
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = self.make_unique_id(entry_id, kategorie)

    def _build_translation_placeholders(self) -> dict[str, str]:
        """Extend the house placeholders with the ``kategorie`` value."""
        placeholders = super()._build_translation_placeholders()
        placeholders["kategorie"] = self._kategorie.value
        return placeholders

    @classmethod
    def make_unique_id(cls, entry_id: str, kategorie: Kategorie | None = None) -> str:
        """Return the stable unique-id including the category segment."""
        if kategorie is None:
            return f"{entry_id}_haus_kategorie"
        return f"{entry_id}_haus_kategorie_{kategorie.value}_jahr"

    @property
    def native_value(self) -> float | None:
        """Return the house-wide yearly total for the target category."""
        return self.coordinator.data["haus"]["pro_kategorie_jahr_eur"].get(
            self._kategorie
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the category id alongside the positions across all parties."""
        positionen: list[dict[str, Any]] = []
        for partei_result in self.coordinator.data["parteien"].values():
            positionen.extend(
                _position_attrs(p)
                for p in partei_result["positionen"]
                if p["kategorie"] == self._kategorie
            )
        return {"kategorie": self._kategorie.value, "positionen": positionen}


# ---------------------------------------------------------------------------
# Abschlag-position sensors (per party, per Kostenposition)
# ---------------------------------------------------------------------------


def _find_position(
    result: ParteiResult | None,
    kp_id: str,
) -> PositionAttribution | None:
    """Return the PositionAttribution for ``kp_id`` in ``result``, if any."""
    if result is None:
        return None
    for pos in result["positionen"]:
        if pos["kostenposition_id"] == kp_id:
            return pos
    return None


class _ParteiAbschlagSensorBase(_EuroPartyMixin, ParteiSensorBase):
    """Shared base for per-party Abschlag sensors.

    Encodes the ``(entry_id, partei_id, kp_id)`` triple into the unique-id
    and exposes the kostenposition's ``bezeichnung`` as a translation
    placeholder so the frontend can render names like
    ``"OG (Simon) - Wasser: Abschlaege 2026"``.
    """

    _zweck = "abschlag"  # overwritten by subclasses via `_suffix`
    _suffix: ClassVar[str]

    def __init__(
        self,
        coordinator: HauskostenCoordinator,
        entry_id: str,
        partei_id: str,
        kp_id: str,
        bezeichnung: str,
    ) -> None:
        """Store the target position and build a position-aware unique-id."""
        self._kp_id = kp_id
        self._bezeichnung = bezeichnung
        super().__init__(coordinator, entry_id, partei_id)
        self._attr_unique_id = self.make_unique_id(entry_id, partei_id, kp_id)

    def _build_translation_placeholders(self) -> dict[str, str]:
        """Extend the party placeholders with the position's ``bezeichnung``."""
        placeholders = super()._build_translation_placeholders()
        placeholders["bezeichnung"] = self._bezeichnung
        return placeholders

    @classmethod
    def make_unique_id(
        cls,
        entry_id: str,
        partei_id: str,
        kp_id: str | None = None,
    ) -> str:
        """Return the stable unique-id including the position segment."""
        if kp_id is None:
            return f"{entry_id}_partei_{partei_id}_abschlag_{cls._suffix}"
        return f"{entry_id}_partei_{partei_id}_abschlag_{kp_id}_{cls._suffix}"

    def _abschlag_value(self, field: str) -> float | None:
        """Read one of the abschlag_* fields from the target position."""
        pos = _find_position(self._partei_result(), self._kp_id)
        if pos is None:
            return None
        return pos.get(field)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the source position id for dashboard drill-down."""
        base = _partei_attrs(self._partei_result(), self.coordinator.data)
        base["kostenposition_id"] = self._kp_id
        base["bezeichnung"] = self._bezeichnung
        return base


class ParteiAbschlagGezahltSensor(_ParteiAbschlagSensorBase):
    """Cumulative prepayments the party has paid this reconciliation period."""

    _suffix = "gezahlt"
    _attr_translation_key = _TRKEY_PARTEI_ABSCHLAG_GEZAHLT
    _attr_icon = "mdi:cash-multiple"

    @property
    def native_value(self) -> float | None:
        """Return this party's prepaid share (EUR)."""
        return self._abschlag_value("abschlag_gezahlt_eur_jahr")


class ParteiAbschlagIstSensor(_ParteiAbschlagSensorBase):
    """Consumption-derived IST cost for this party in the current period."""

    _suffix = "ist"
    _attr_translation_key = _TRKEY_PARTEI_ABSCHLAG_IST
    _attr_icon = "mdi:gauge"

    @property
    def native_value(self) -> float | None:
        """Return this party's IST share (EUR)."""
        return self._abschlag_value("abschlag_ist_eur_jahr")


class ParteiAbschlagSaldoSensor(_ParteiAbschlagSensorBase):
    """Expected reconciliation saldo (IST - gezahlt) for this party.

    Positive = Nachzahlung expected, negative = Guthaben expected.
    """

    _suffix = "saldo"
    _attr_translation_key = _TRKEY_PARTEI_ABSCHLAG_SALDO
    _attr_icon = "mdi:scale-balance"

    @property
    def native_value(self) -> float | None:
        """Return this party's saldo (EUR)."""
        return self._abschlag_value("abschlag_saldo_eur_jahr")


# ---------------------------------------------------------------------------
# Abschlag house-wide sensors (aggregated across parties per Kostenposition)
# ---------------------------------------------------------------------------


def _sum_abschlag_field(
    data: CoordinatorData | None,
    kp_id: str,
    field: str,
) -> float | None:
    """Sum one abschlag_* field across all parties for a position.

    Returns ``None`` when no party has a usable value (e.g. IST is None
    everywhere because the Statistics API has no data). Summing a mix of
    populated and None values is treated as "whatever is populated" -- the
    gezahlt field is always populated when the position is configured,
    while IST / saldo may be None.
    """
    if data is None:
        return None
    total: float | None = None
    for partei_result in data["parteien"].values():
        pos = _find_position(partei_result, kp_id)
        if pos is None:
            continue
        value = pos.get(field)
        if value is None:
            continue
        total = value if total is None else total + value
    return total if total is None else round(total, 2)


class _HausAbschlagSensorBase(_EuroHausMixin, HausSensorBase):
    """Shared base for house-wide Abschlag aggregates per position."""

    _zweck = "abschlag"  # placeholder; real id uses _suffix + kp_id
    _suffix: ClassVar[str]

    def __init__(
        self,
        coordinator: HauskostenCoordinator,
        entry_id: str,
        kp_id: str,
        bezeichnung: str,
    ) -> None:
        """Store the position id and build a position-aware unique-id."""
        self._kp_id = kp_id
        self._bezeichnung = bezeichnung
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = self.make_abschlag_unique_id(entry_id, kp_id)

    def _build_translation_placeholders(self) -> dict[str, str]:
        """Extend house placeholders with the position's ``bezeichnung``."""
        placeholders = super()._build_translation_placeholders()
        placeholders["bezeichnung"] = self._bezeichnung
        return placeholders

    @classmethod
    def make_abschlag_unique_id(cls, entry_id: str, kp_id: str) -> str:
        """Return the stable unique-id including the position segment."""
        return f"{entry_id}_haus_abschlag_{kp_id}_{cls._suffix}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the source position id + label for dashboard drill-down."""
        return {
            "kostenposition_id": self._kp_id,
            "bezeichnung": self._bezeichnung,
        }


class HausAbschlagGezahltSensor(_HausAbschlagSensorBase):
    """House-wide total of prepayments this period for one Kostenposition."""

    _suffix = "gezahlt"
    _attr_translation_key = _TRKEY_HAUS_ABSCHLAG_GEZAHLT
    _attr_icon = "mdi:cash-multiple"

    @property
    def native_value(self) -> float | None:
        """Return the sum of prepayments across all parties."""
        return _sum_abschlag_field(
            self.coordinator.data,
            self._kp_id,
            "abschlag_gezahlt_eur_jahr",
        )


class HausAbschlagIstSensor(_HausAbschlagSensorBase):
    """House-wide IST cost this period for one Kostenposition."""

    _suffix = "ist"
    _attr_translation_key = _TRKEY_HAUS_ABSCHLAG_IST
    _attr_icon = "mdi:gauge"

    @property
    def native_value(self) -> float | None:
        """Return the sum of IST costs across all parties."""
        return _sum_abschlag_field(
            self.coordinator.data,
            self._kp_id,
            "abschlag_ist_eur_jahr",
        )


class HausAbschlagSaldoSensor(_HausAbschlagSensorBase):
    """House-wide saldo this period for one Kostenposition."""

    _suffix = "saldo"
    _attr_translation_key = _TRKEY_HAUS_ABSCHLAG_SALDO
    _attr_icon = "mdi:scale-balance"

    @property
    def native_value(self) -> float | None:
        """Return the sum of saldi across all parties."""
        return _sum_abschlag_field(
            self.coordinator.data,
            self._kp_id,
            "abschlag_saldo_eur_jahr",
        )


# ---------------------------------------------------------------------------
# Attribute helpers
# ---------------------------------------------------------------------------


def _partei_attrs(
    result: ParteiResult | None,
    data: CoordinatorData | None,
    *,
    include_positionen: bool = False,
) -> dict[str, Any]:
    """Build the base attribute dict for a party-scoped sensor.

    Args:
        result: The :class:`ParteiResult` currently attached to the sensor,
            or ``None`` if the party disappeared (then the sensor should
            already be unavailable; we still avoid KeyErrors).
        data: The coordinator snapshot carrying ``computed_at``.
        include_positionen: Whether to embed the full position list. Only
            the ``jahr_aktuell`` sensor needs it.

    Returns:
        A dictionary safe to expose as ``extra_state_attributes``.
    """
    if result is None:
        return {}
    attrs: dict[str, Any] = {
        "partei_id": result["partei"]["id"],
        "partei_name": result["partei"]["name"],
    }
    if data is not None:
        attrs["computed_at"] = data["computed_at"].isoformat()
    if include_positionen:
        attrs["positionen"] = [_position_attrs(p) for p in result["positionen"]]
    return attrs


def _position_attrs(pos: PositionAttribution) -> dict[str, Any]:
    """Shape a :class:`PositionAttribution` for dashboard consumption."""
    return {
        "id": pos["kostenposition_id"],
        "bezeichnung": pos["bezeichnung"],
        "kategorie": pos["kategorie"],
        "anteil_eur_jahr": pos["anteil_eur_jahr"],
        "verteilschluessel": pos["verteilschluessel_verwendet"].value,
        "error": pos["error"],
    }


# ---------------------------------------------------------------------------
# Registration tables
# ---------------------------------------------------------------------------


#: Non-category party sensor classes -- one instance per party.
_PARTEI_SENSOR_CLASSES: tuple[type[ParteiSensorBase], ...] = (
    ParteiMonatSensor,
    ParteiJahrAktuellSensor,
    ParteiJahrBudgetSensor,
    ParteiNaechsteFaelligkeitSensor,
)

#: Per-party Abschlag drill-down classes -- one instance per (party, kp).
_PARTEI_ABSCHLAG_SENSOR_CLASSES: tuple[type[_ParteiAbschlagSensorBase], ...] = (
    ParteiAbschlagGezahltSensor,
    ParteiAbschlagIstSensor,
    ParteiAbschlagSaldoSensor,
)

#: Non-category house-wide sensor classes -- one instance per entry.
_HAUS_SENSOR_CLASSES: tuple[type[HausSensorBase], ...] = (
    HausJahrGesamtSensor,
    HausJahrBudgetSensor,
    HausNaechsteFaelligkeitSensor,
)

#: House-wide Abschlag aggregate classes -- one instance per Kostenposition.
_HAUS_ABSCHLAG_SENSOR_CLASSES: tuple[type[_HausAbschlagSensorBase], ...] = (
    HausAbschlagGezahltSensor,
    HausAbschlagIstSensor,
    HausAbschlagSaldoSensor,
)
