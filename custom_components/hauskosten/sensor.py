"""Sensor platform stub for the hauskosten integration.

This module is intentionally minimal. Phase 1.7 (``integration-architect``)
wires up :mod:`__init__` to forward the ``sensor`` platform so the config
entry can be installed end-to-end; Phase 1.8 (``sensor-dev``) will replace
this stub with dynamic per-partei / per-kategorie / haus-wide sensors
driven by :class:`.coordinator.HauskostenCoordinator`.

Until then, ``async_setup_entry`` returns without adding any entities so
that the integration can be loaded, config subentries created, and the
coordinator exercised via service calls and tests even before sensors exist.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

__all__ = ["async_setup_entry"]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback | Callable[..., None],
) -> None:
    """Set up the sensor platform for a config entry.

    Placeholder implementation -- the real entity factory lands in Phase 1.8.
    Returning without calling ``async_add_entities`` is the documented way to
    declare "this platform is available but has nothing to add right now".

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being set up.
        async_add_entities: Callback used by the real implementation to
            register entities with the platform.
    """
    _ = hass
    _ = async_add_entities
    _LOGGER.debug(
        "Sensor platform stub for entry %s -- no entities yet (phase 1.8)",
        entry.entry_id,
    )
