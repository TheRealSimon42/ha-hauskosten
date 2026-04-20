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

from .calculations import abschlaege_gezahlt
from .const import (
    CONF_ABRECHNUNGSZEITRAUM_DAUER_MONATE,
    CONF_ABRECHNUNGSZEITRAUM_START,
    CONF_MONATLICHER_ABSCHLAG_EUR,
    DEFAULT_ABRECHNUNGSZEITRAUM_DAUER_MONATE,
    DOMAIN,
    SERVICE_ADD_EINMALIG,
    SERVICE_JAHRESABRECHNUNG_BUCHEN,
    SERVICE_MARK_PAID,
    SUBENTRY_KOSTENPOSITION,
)
from .models import Betragsmodus, Kategorie, Verteilung, Zuordnung

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry, ConfigSubentry

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
ATTR_FINAL_BETRAG_EUR = "final_betrag_eur"
ATTR_ABRECHNUNGSDATUM = "abrechnungsdatum"


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


_JAHRESABRECHNUNG_BUCHEN_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Required(ATTR_KOSTENPOSITION_ID): vol.All(cv.string, vol.Length(min=1)),
        vol.Required(ATTR_FINAL_BETRAG_EUR): vol.Coerce(float),
        vol.Optional(ATTR_ABRECHNUNGSDATUM): cv.date,
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
    hass.services.async_register(
        DOMAIN,
        SERVICE_JAHRESABRECHNUNG_BUCHEN,
        _make_jahresabrechnung_buchen_handler(hass),
        schema=_JAHRESABRECHNUNG_BUCHEN_SCHEMA,
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
    if hass.services.has_service(DOMAIN, SERVICE_JAHRESABRECHNUNG_BUCHEN):
        hass.services.async_remove(DOMAIN, SERVICE_JAHRESABRECHNUNG_BUCHEN)
    _LOGGER.debug("Unregistered hauskosten services")


# ---------------------------------------------------------------------------
# Entry resolution
# ---------------------------------------------------------------------------


def _resolve_entry_slot_with_entry(
    hass: HomeAssistant,
    entry_id: str | None,
) -> tuple[HauskostenStore, HauskostenCoordinator, ConfigEntry]:
    """Return (store, coordinator, entry) for ``entry_id`` or raise.

    Wrapper around :func:`_resolve_entry_slot` that also returns the
    :class:`ConfigEntry` instance, used by services that need to read or
    mutate subentry data (e.g. ``jahresabrechnung_buchen``).
    """
    store, coordinator = _resolve_entry_slot(hass, entry_id)
    assert coordinator.config_entry is not None  # noqa: S101 - set by setup
    return store, coordinator, coordinator.config_entry


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
        slot: dict[str, Any] = next(iter(entries.values()))
    else:
        resolved = entries.get(entry_id)
        if resolved is None:
            raise ServiceValidationError(f"Unknown hauskosten entry_id: {entry_id}")
        slot = resolved
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


def _make_jahresabrechnung_buchen_handler(hass: HomeAssistant) -> Any:
    """Return the ``hauskosten.jahresabrechnung_buchen`` handler.

    On invocation the handler:

    1. Resolves the ABSCHLAG kostenposition by id (rejects other modes).
    2. Calculates the cumulative prepayment total for the currently
       running reconciliation period.
    3. Writes a new :class:`.models.AdHocKosten` for the
       ``final_betrag - gezahlt`` Nachzahlung (only when positive; credits
       roll silently and are visible in the log).
    4. Advances the ``abrechnungszeitraum_start`` by
       ``abrechnungszeitraum_dauer_monate`` months so the next period
       starts immediately.
    5. Requests a coordinator refresh so sensors pick up the new state.
    """

    async def _handler(call: ServiceCall) -> None:
        """Book the annual reconciliation for an ABSCHLAG position."""
        store, coordinator, entry = _resolve_entry_slot_with_entry(
            hass,
            call.data.get(ATTR_ENTRY_ID),
        )
        kp_id = call.data[ATTR_KOSTENPOSITION_ID]
        final_betrag = float(call.data[ATTR_FINAL_BETRAG_EUR])
        if ATTR_ABRECHNUNGSDATUM in call.data:
            abrechnungsdatum = _ensure_date(
                call.data[ATTR_ABRECHNUNGSDATUM],
                field=ATTR_ABRECHNUNGSDATUM,
            )
        else:
            abrechnungsdatum = date.today()

        subentry = _find_abschlag_subentry(entry, kp_id)
        gezahlt = _gezahlt_snapshot(subentry, abrechnungsdatum)
        delta = round(final_betrag - gezahlt, 2)

        if delta > 0:
            await _write_nachzahlung_adhoc(
                store=store,
                subentry=subentry,
                delta_eur=delta,
                abrechnungsdatum=abrechnungsdatum,
                gezahlt=gezahlt,
                final_betrag=final_betrag,
            )
        else:
            _LOGGER.info(
                "Jahresabrechnung kp=%s: no Nachzahlung (delta=%.2f, "
                "gezahlt=%.2f, final=%.2f) - rolling period only",
                kp_id,
                delta,
                gezahlt,
                final_betrag,
            )

        _roll_abrechnungszeitraum(hass, entry, subentry)
        await coordinator.async_request_refresh()

    return _handler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_abschlag_subentry(
    entry: ConfigEntry,
    kp_id: str,
) -> ConfigSubentry:
    """Return the ABSCHLAG Kostenposition subentry matching ``kp_id`` or raise."""
    subentry = entry.subentries.get(kp_id)
    if subentry is None or subentry.subentry_type != SUBENTRY_KOSTENPOSITION:
        raise ServiceValidationError(
            f"Unknown kostenposition_id: {kp_id}",
        )
    if subentry.data.get("betragsmodus") != Betragsmodus.ABSCHLAG.value:
        raise ServiceValidationError(
            f"Kostenposition {kp_id} is not in ABSCHLAG mode",
        )
    return subentry


def _gezahlt_snapshot(subentry: ConfigSubentry, stichtag: date) -> float:
    """Return the cumulative prepayment amount at ``stichtag``."""
    data = subentry.data
    monatlich_raw = data.get(CONF_MONATLICHER_ABSCHLAG_EUR)
    if monatlich_raw is None:
        raise ServiceValidationError(
            f"Kostenposition {subentry.subentry_id} has no monthly prepayment",
        )
    start_raw = data.get(CONF_ABRECHNUNGSZEITRAUM_START)
    if start_raw is None:
        raise ServiceValidationError(
            f"Kostenposition {subentry.subentry_id} has no reconciliation anchor",
        )
    try:
        start = date.fromisoformat(start_raw)
    except (TypeError, ValueError) as err:
        raise ServiceValidationError(
            f"Invalid abrechnungszeitraum_start on {subentry.subentry_id}",
        ) from err
    dauer = (
        data.get(CONF_ABRECHNUNGSZEITRAUM_DAUER_MONATE)
        or DEFAULT_ABRECHNUNGSZEITRAUM_DAUER_MONATE
    )
    return abschlaege_gezahlt(
        float(monatlich_raw),
        start,
        int(dauer),
        stichtag,
    )


async def _write_nachzahlung_adhoc(
    *,
    store: HauskostenStore,
    subentry: ConfigSubentry,
    delta_eur: float,
    abrechnungsdatum: date,
    gezahlt: float,
    final_betrag: float,
) -> None:
    """Persist the ``delta_eur`` Nachzahlung as an :class:`AdHocKosten`."""
    data = subentry.data
    record: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "bezeichnung": (
            f"Jahresabrechnung {data.get('bezeichnung', '')} {abrechnungsdatum.year}"
        ).strip(),
        "kategorie": str(data.get("kategorie", Kategorie.SONSTIGES.value)),
        "betrag_eur": float(delta_eur),
        "datum": abrechnungsdatum,
        "zuordnung": str(data.get("zuordnung", Zuordnung.HAUS.value)),
        "zuordnung_partei_id": data.get("zuordnung_partei_id"),
        "verteilung": str(data.get("verteilung", Verteilung.GLEICH.value)),
        "bezahlt_am": None,
        "notiz": (
            f"Final: {final_betrag:.2f} EUR, "
            f"Abschlaege: {gezahlt:.2f} EUR, "
            f"Nachzahlung: {delta_eur:.2f} EUR"
        ),
    }
    await store.async_add_adhoc(record)
    _LOGGER.info(
        "Jahresabrechnung kp=%s: booked Nachzahlung %.2f EUR (final=%.2f, "
        "gezahlt=%.2f)",
        subentry.subentry_id,
        delta_eur,
        final_betrag,
        gezahlt,
    )


def _roll_abrechnungszeitraum(
    hass: HomeAssistant,
    entry: ConfigEntry,
    subentry: ConfigSubentry,
) -> None:
    """Advance the reconciliation period anchor by its duration.

    Done in-place on the subentry via
    ``hass.config_entries.async_update_subentry`` so the next coordinator
    refresh reads a fresh period. Uses a tolerant month-shift that clamps
    the day-of-month for shorter target months (e.g. 31-Jan + 1 -> 28-Feb).
    """
    from calendar import monthrange  # noqa: PLC0415 - small, only used here

    data = dict(subentry.data)
    start_raw = data.get(CONF_ABRECHNUNGSZEITRAUM_START)
    dauer_raw = data.get(CONF_ABRECHNUNGSZEITRAUM_DAUER_MONATE)
    if not start_raw or not dauer_raw:  # pragma: no cover - guarded upstream
        return
    start = date.fromisoformat(start_raw)
    dauer = int(dauer_raw)
    target_year = start.year + (start.month - 1 + dauer) // 12
    target_month = (start.month - 1 + dauer) % 12 + 1
    last_day = monthrange(target_year, target_month)[1]
    new_start = date(target_year, target_month, min(start.day, last_day))
    data[CONF_ABRECHNUNGSZEITRAUM_START] = new_start.isoformat()
    hass.config_entries.async_update_subentry(entry, subentry, data=data)


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
