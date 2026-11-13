"""Config flow for Hamlib CAT."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    BAUDRATES,
    CONF_BAUDRATE,
    CONF_CIV_ADDRESS,
    CONF_CONNECTION,
    CONF_PROTOCOL,
    CONF_SCAN_INTERVAL,
    CONF_SERIAL_PORT,
    CONNECTION_RIGCTLD,
    CONNECTION_SERIAL,
    DEFAULT_BAUDRATE,
    DEFAULT_CIV_ADDRESS,
    DEFAULT_RIGCTLD_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    PROTOCOL_ICOM_CIV,
    PROTOCOL_RIGCTLD,
    SERIAL_PROTOCOLS,
)
from .rig import RigError

_LOGGER = logging.getLogger(__name__)


def _serial_port_selector() -> Any:
    """HA 2026.5+ SerialPortSelector lists AUX-2 serial-proxy ports; else free text."""
    if hasattr(selector, "SerialPortSelector"):
        return selector.SerialPortSelector()
    return selector.TextSelector()


def _valid_civ_address(value: str) -> bool:
    try:
        return 0x01 <= int(value, 16) <= 0xDF
    except ValueError:
        return False


async def _async_test(data: dict[str, Any]) -> None:
    """Open the port and read the frequency once."""
    from . import build_client  # avoid import cycle at module load

    client = build_client(data)
    try:
        await client.async_read_state()
    finally:
        await client.async_close()


class HamlibCatConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a radio."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=[CONNECTION_SERIAL, CONNECTION_RIGCTLD])

    async def async_step_serial(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            civ = user_input.get(CONF_CIV_ADDRESS, DEFAULT_CIV_ADDRESS).strip().upper().removeprefix("0X")
            user_input[CONF_CIV_ADDRESS] = civ
            if user_input[CONF_PROTOCOL] == PROTOCOL_ICOM_CIV and not _valid_civ_address(civ):
                errors[CONF_CIV_ADDRESS] = "invalid_civ_address"
            else:
                unique = user_input[CONF_SERIAL_PORT]
                if user_input[CONF_PROTOCOL] == PROTOCOL_ICOM_CIV:
                    unique = f"{unique}#civ{civ}"  # several Icoms can share one CI-V bus
                await self.async_set_unique_id(unique)
                self._abort_if_unique_id_configured()
                data = {CONF_CONNECTION: CONNECTION_SERIAL, **user_input}
                errors = await self._try(data)
                if not errors:
                    name = data.pop(CONF_NAME)
                    return self.async_create_entry(title=name, data=data)

        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "Ham radio")): str,
                vol.Required(CONF_SERIAL_PORT, default=defaults.get(CONF_SERIAL_PORT, vol.UNDEFINED)): _serial_port_selector(),
                vol.Required(CONF_PROTOCOL, default=defaults.get(CONF_PROTOCOL, SERIAL_PROTOCOLS[0])): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=SERIAL_PROTOCOLS,
                        translation_key=CONF_PROTOCOL,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(CONF_BAUDRATE, default=defaults.get(CONF_BAUDRATE, DEFAULT_BAUDRATE)): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=BAUDRATES, mode=selector.SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional(CONF_CIV_ADDRESS, default=defaults.get(CONF_CIV_ADDRESS, DEFAULT_CIV_ADDRESS)): str,
            }
        )
        return self.async_show_form(step_id="serial", data_schema=schema, errors=errors)

    async def async_step_rigctld(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()
            data = {CONF_CONNECTION: CONNECTION_RIGCTLD, CONF_PROTOCOL: PROTOCOL_RIGCTLD, **user_input}
            errors = await self._try(data)
            if not errors:
                name = data.pop(CONF_NAME)
                return self.async_create_entry(title=name, data=data)

        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "Ham radio")): str,
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, vol.UNDEFINED)): str,
                vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_RIGCTLD_PORT)): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
            }
        )
        return self.async_show_form(step_id="rigctld", data_schema=schema, errors=errors)

    async def _try(self, data: dict[str, Any]) -> dict[str, str]:
        try:
            await _async_test(data)
        except RigError as err:
            _LOGGER.warning("Could not talk to the radio: %s", err)
            return {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected error talking to the radio")
            return {"base": "unknown"}
        return {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return HamlibCatOptionsFlow()


class HamlibCatOptionsFlow(OptionsFlow):
    """Polling interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=60,
                            step=0.5,
                            unit_of_measurement="s",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    )
                }
            ),
        )
