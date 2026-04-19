"""Config flow for ha-hauskosten.

Minimal stub to satisfy hassfest — full implementation (subentries for
partei/kostenposition, reconfigure-flow, validation matrix) lands in
phase 1.11 via the config-flow-dev agent. See docs/ONBOARDING.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow

from .const import CONF_HAUS_NAME, DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigFlowResult


class HauskostenConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Hauskosten config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial user step (create a house)."""
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({vol.Required(CONF_HAUS_NAME): str}),
            )

        await self.async_set_unique_id(user_input[CONF_HAUS_NAME])
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=user_input[CONF_HAUS_NAME],
            data=user_input,
        )
