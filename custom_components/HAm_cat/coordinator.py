"""Polling coordinator for Hamlib CAT."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .rig import RigClient, RigError, RigState

_LOGGER = logging.getLogger(__name__)


class HamlibCatCoordinator(DataUpdateCoordinator[RigState]):
    """Polls the radio and fans the state out to entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: RigClient) -> None:
        interval = float(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=interval),
            always_update=False,  # only write state when the radio actually changes
        )
        self.client = client

    async def _async_update_data(self) -> RigState:
        try:
            return await self.client.async_read_state()
        except RigError as err:
            raise UpdateFailed(str(err)) from err
