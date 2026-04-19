"""Hauskosten Integration for Home Assistant.

Faire Kostenverteilung fuer Mehrfamilienhaeuser.
Siehe AGENTS.md fuer Architektur und docs/ONBOARDING.md fuer Einstieg.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ha-hauskosten from a config entry.

    TODO: Wird von coordinator-dev in Phase 1 implementiert.
    Siehe docs/ONBOARDING.md Schritt 2.
    """
    _ = hass  # reserved for phase 1 implementation
    _LOGGER.debug("Setting up %s (entry_id=%s)", DOMAIN, entry.entry_id)
    raise NotImplementedError("Implementation pending - see docs/ONBOARDING.md")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entry schema.

    TODO: Wird von integration-architect bei Schema-Aenderungen erweitert.
    """
    _ = hass  # reserved for phase 1 migration logic
    _LOGGER.debug("Migration for entry %s: schema v%s", entry.entry_id, entry.version)
    return True
