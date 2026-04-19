"""Service actions for the hauskosten integration.

Authoritative spec:
* ``docs/ARCHITECTURE.md`` -- "Service Actions" section listing the public
  service surface.
* ``docs/DATA_MODEL.md``   -- :class:`AdHocKosten` schema; fields translate
  one-to-one to the ``hauskosten.add_einmalig`` service payload.
* ``AGENTS.md`` hard constraint #4 -- every async error path logs via
  ``_LOGGER``; constraint #7 -- user-facing strings via translations (service
  names / descriptions live in :file:`strings.json`).

The services operate on a specific config entry, selected by the
``entry_id`` service field. When only a single config entry is loaded, the
caller may omit ``entry_id`` and the integration falls back to that entry.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.const import CONF_ENTITY_ID  # noqa: F401 -- reserved for future use
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    SERVICE_ADD_EINMALIG,
    SERVICE_MARK_PAID,
)
from .models import Kategorie, Verteilung, Zuordnung

if TYPE_CHECKING:
    from .coordinator import HauskostenCoordinator
    from .storage import HauskostenStore

__all__ = ["async_register_services", "async_unregister_services"]

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Service field names
# ---------------------------------------------------------------------------

ATTR_ENTRY_ID = "entry_id"
ATTR_BEZEICHNUNG = "bezeichnung"
ATTR_KATEGORIE = "kategorie"
ATTR_BETRAG_EUR = "betrag_eur"
ATTR_DATUM = "datum"
ATTR_ZUORDNUNG = "zuordnung"
ATTR_ZUORDNUNG_PARTEI_ID = "zuordnung_partei_id"
ATTR_VERTEILUNG = "verteilung"
ATTR_NOTIZ = "notiz"
ATTR_KOSTENPOSITION_ID = "kostenposition_id"
ATTR_BEZAHLT_AM = "bezahlt_am"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


_KATEGORIE_VALUES = [k.value for k in Kategorie]
_ZUORDNUNG_VALUES = [z.value for z in Zuordnung]
_VERTEILUNG_VALUES = [v.value for v in Verteilung]


_ADD_EINMALIG_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Required(ATTR_BEZEICHNUNG): vol.All(cv.string, vol.Length(min=1)),
        vol.Required(ATTR_KATEGORIE): vol.In(_KATEGORIE_VALUES),
        vol.Required(ATTR_BETRAG_EUR): vol.Coerce(float),
        vol.Required(ATTR_DATUM): cv.date,
        vol.Required(ATTR_ZUORDNUNG): vol.In(_ZUORDNUNG_VALUES),
        vol.Optional(ATTR_ZUORDNUNG_PARTEI_ID): cv.string,
        vol.Required(ATTR_VERTEILUNG): vol.In(_VERTEILUNG_VALUES),
        vol.Optional(ATTR_NOTIZ): cv.string,
    }
)


_MARK_PAID_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Required(ATTR_KOSTENPOSITION_ID): vol.All(cv.string, vol.Length(min=1)),
        vol.Required(ATTR_BEZAHLT_AM): cv.date,
    }
)


# ---------------------------------------------------------------------------
# Public registration API
# ---------------------------------------------------------------------------


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the public service actions if not already registered.

    Called from :func:`.async_setup_entry`; calling again after the first
    registration is a no-op so the module is safe to invoke per entry.
    """
    if hass.services.has_service(DOMAIN, SERVICE_ADD_EINMALIG):
        return

    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_EINMALIG,
        _make_add_einmalig_handler(hass),
        schema=_ADD_EINMALIG_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_MARK_PAID,
        _make_mark_paid_handler(hass),
        schema=_MARK_PAID_SCHEMA,
    )
    _LOGGER.debug("Registered hauskosten services")


@callback
def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove the service registrations.

    Invoked by :func:`.async_unload_entry` when the final entry unloads, so
    services do not linger after the integration is removed.
    """
    if hass.services.has_service(DOMAIN, SERVICE_ADD_EINMALIG):
        hass.services.async_remove(DOMAIN, SERVICE_ADD_EINMALIG)
    if hass.services.has_service(DOMAIN, SERVICE_MARK_PAID):
        hass.services.async_remove(DOMAIN, SERVICE_MARK_PAID)
    _LOGGER.debug("Unregistered hauskosten services")


# ---------------------------------------------------------------------------
# Entry resolution
# ---------------------------------------------------------------------------


def _resolve_entry_slot(
    hass: HomeAssistant,
    entry_id: str | None,
) -> tuple[HauskostenStore, HauskostenCoordinator]:
    """Return the (store, coordinator) for ``entry_id`` or raise.

    Args:
        hass: The Home Assistant instance.
        entry_id: Explicit entry id, or ``None`` to auto-pick when there is
            exactly one loaded entry.

    Raises:
        ServiceValidationError: If the entry_id is unknown, missing when
            required (multiple entries loaded), or the entry is not set up.
    """
    entries: dict[str, dict[str, Any]] = hass.data.get(DOMAIN, {})
    if not entries:
        raise ServiceValidationError(
            f"{DOMAIN} integration has no loaded config entries"
        )

    if entry_id is None:
        if len(entries) != 1:
            raise ServiceValidationError(
                "entry_id is required when multiple hauskosten entries exist"
            )
        slot = next(iter(entries.values()))
    else:
        slot = entries.get(entry_id)
        if slot is None:
            raise ServiceValidationError(f"Unknown hauskosten entry_id: {entry_id}")
    return slot["store"], slot["coordinator"]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _make_add_einmalig_handler(hass: HomeAssistant) -> Any:
    """Return the ``hauskosten.add_einmalig`` handler bound to ``hass``."""

    async def _handler(call: ServiceCall) -> None:
        """Append an ad-hoc cost record and schedule a coordinator refresh."""
        store, coordinator = _resolve_entry_slot(hass, call.data.get(ATTR_ENTRY_ID))
        datum = _ensure_date(call.data[ATTR_DATUM], field=ATTR_DATUM)
        record: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "bezeichnung": call.data[ATTR_BEZEICHNUNG],
            "kategorie": call.data[ATTR_KATEGORIE],
            "betrag_eur": float(call.data[ATTR_BETRAG_EUR]),
            "datum": datum,
            "zuordnung": call.data[ATTR_ZUORDNUNG],
            "zuordnung_partei_id": call.data.get(ATTR_ZUORDNUNG_PARTEI_ID),
            "verteilung": call.data[ATTR_VERTEILUNG],
            "bezahlt_am": None,
            "notiz": call.data.get(ATTR_NOTIZ),
        }
        try:
            await store.async_add_adhoc(record)
        except ValueError as err:
            _LOGGER.exception("add_einmalig rejected")
            raise ServiceValidationError(str(err)) from err
        await coordinator.async_request_refresh()

    return _handler


def _make_mark_paid_handler(hass: HomeAssistant) -> Any:
    """Return the ``hauskosten.mark_paid`` handler bound to ``hass``."""

    async def _handler(call: ServiceCall) -> None:
        """Record a payment timestamp for a kostenposition."""
        store, coordinator = _resolve_entry_slot(hass, call.data.get(ATTR_ENTRY_ID))
        bezahlt_am = _ensure_date(call.data[ATTR_BEZAHLT_AM], field=ATTR_BEZAHLT_AM)
        try:
            await store.async_mark_paid(
                call.data[ATTR_KOSTENPOSITION_ID],
                bezahlt_am,
            )
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.exception("mark_paid failed")
            raise ServiceValidationError(str(err)) from err
        await coordinator.async_request_refresh()

    return _handler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_date(value: Any, *, field: str) -> date:
    """Return ``value`` as a :class:`datetime.date`.

    The HA ``cv.date`` validator accepts both ``date`` instances and ISO
    strings. The YAML pathway often submits plain strings even after schema
    coercion, so we accept both and fail loud otherwise.
    """
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as err:
            raise ServiceValidationError(
                f"{field} must be an ISO date (YYYY-MM-DD)"
            ) from err
    raise ServiceValidationError(f"{field} must be a date")  # pragma: no cover
