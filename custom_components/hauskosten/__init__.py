"""Hauskosten integration for Home Assistant.

Faire Kostenverteilung fuer Mehrfamilienhaeuser.
Siehe AGENTS.md fuer Architektur und docs/ONBOARDING.md fuer Einstieg.

This module owns the integration lifecycle: it creates the per-entry
:class:`HauskostenStore` + :class:`HauskostenCoordinator`, wires the
state-change listener, forwards platforms and (un)registers the public
service actions. All heavy lifting lives in the modules it composes --
this file is intentionally small.

Lifecycle (see ``docs/ARCHITECTURE.md``):

1. ``async_setup_entry`` -- load store, build coordinator, first refresh,
   install state listener, register services, forward platforms.
2. ``async_entry_update_listener`` -- re-wire the state listener and
   request a refresh whenever the entry or one of its subentries changes.
3. ``async_unload_entry`` -- shut down the listener, unload platforms,
   drop the ``hass.data`` slot, unregister services when the last entry
   goes.
4. ``async_migrate_entry`` -- bump schema versions between breaking
   changes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_SCHEMA_VERSION, DOMAIN
from .coordinator import HauskostenCoordinator
from .services import async_register_services, async_unregister_services
from .storage import HauskostenStore

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

__all__ = [
    "PLATFORMS",
    "async_migrate_entry",
    "async_setup_entry",
    "async_unload_entry",
]

_LOGGER = logging.getLogger(__name__)

#: Platforms forwarded from the integration entry point. Phase 1.8 fleshes
#: out ``sensor.py``; the stub in place today allows setup to succeed.
PLATFORMS: list[str] = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ha-hauskosten from a config entry.

    Builds the per-entry :class:`HauskostenStore` and
    :class:`HauskostenCoordinator`, performs the first refresh, registers
    the state-change listener, and forwards the configured platforms. On
    any transient failure this raises :class:`ConfigEntryNotReady` so Home
    Assistant retries the setup.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being set up.

    Returns:
        ``True`` if setup succeeded.

    Raises:
        ConfigEntryNotReady: If the persistent store cannot be loaded. HA
            will retry the setup after a back-off.
    """
    _LOGGER.debug("Setting up %s (entry_id=%s)", DOMAIN, entry.entry_id)

    store = HauskostenStore(hass, entry.entry_id)
    try:
        await store.async_load()
    except Exception as err:
        _LOGGER.exception("Failed to load store for entry %s", entry.entry_id)
        raise ConfigEntryNotReady(f"hauskosten store load failed: {err}") from err

    coordinator = HauskostenCoordinator(hass, entry, store)
    await coordinator.async_config_entry_first_refresh()
    coordinator.async_setup_state_listener()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "store": store,
        "coordinator": coordinator,
    }

    entry.async_on_unload(entry.add_update_listener(async_entry_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async_register_services(hass)

    _LOGGER.debug("Setup complete for %s (entry_id=%s)", DOMAIN, entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    Tears down the state-change listener, unloads the forwarded platforms
    and drops the entry's slot in ``hass.data``. When the last entry is
    unloaded the service actions are removed as well.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being unloaded.

    Returns:
        ``True`` when every platform unloaded cleanly.
    """
    _LOGGER.debug("Unloading %s (entry_id=%s)", DOMAIN, entry.entry_id)

    slot: dict[str, Any] | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if slot is not None:
        coordinator: HauskostenCoordinator | None = slot.get("coordinator")
        if coordinator is not None:  # pragma: no branch - always populated by setup
            coordinator.async_shutdown_listener()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if not hass.data.get(DOMAIN):
            async_unregister_services(hass)

    return unload_ok


async def async_entry_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config-entry or subentry updates.

    Re-wires the coordinator's state listener (the set of tracked
    consumption entities can change when the user adds / removes
    kostenpositionen) and requests an immediate refresh so sensors pick up
    the new configuration without waiting for the polling interval.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry that was just updated.
    """
    _LOGGER.debug(
        "Update received for %s (entry_id=%s) -- refreshing coordinator",
        DOMAIN,
        entry.entry_id,
    )
    slot: dict[str, Any] | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if slot is None:  # pragma: no cover - defensive; fires only mid-unload
        _LOGGER.debug("Update listener fired for unloaded entry %s", entry.entry_id)
        return
    coordinator: HauskostenCoordinator = slot["coordinator"]
    coordinator.async_setup_state_listener()
    await coordinator.async_request_refresh()


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an old config-entry schema to the current version.

    The integration currently ships schema version :data:`CONF_SCHEMA_VERSION`
    (= 1). Entries written by a newer version (e.g. after a downgrade) are
    rejected rather than silently mis-read. Future schema bumps extend the
    logic here -- see ``docs/DATA_MODEL.md`` for the migration contract.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being migrated.

    Returns:
        ``True`` when the entry is (already) at the current schema version,
        ``False`` when its version is newer than this integration knows.
    """
    _ = hass  # reserved for future migration steps
    _LOGGER.debug(
        "Migration requested for entry %s: schema v%s -> v%s",
        entry.entry_id,
        entry.version,
        CONF_SCHEMA_VERSION,
    )

    if entry.version > CONF_SCHEMA_VERSION:
        _LOGGER.error(
            "Cannot downgrade entry %s from schema v%s to v%s",
            entry.entry_id,
            entry.version,
            CONF_SCHEMA_VERSION,
        )
        return False

    # Schema v1 is the current version -- nothing to migrate yet. Future
    # breaking changes add their upgrade branch above this comment.
    return True
