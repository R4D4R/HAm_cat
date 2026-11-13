"""Hamlib CAT: read a ham radio's frequency over an AUX-2 serial proxy or rigctld."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BAUDRATE,
    CONF_CIV_ADDRESS,
    CONF_CONNECTION,
    CONF_PROTOCOL,
    CONF_SERIAL_PORT,
    CONNECTION_RIGCTLD,
    DEFAULT_BAUDRATE,
    DEFAULT_CIV_ADDRESS,
    PROTOCOL_ICOM_CIV,
    PROTOCOL_KENWOOD,
    PROTOCOL_YAESU_LEGACY,
    PROTOCOL_YAESU_NEWCAT,
)
from .coordinator import HamlibCatCoordinator
from .rig import (
    Driver,
    IcomCivDriver,
    KenwoodDriver,
    RigClient,
    RigctldDriver,
    SerialTransport,
    TcpTransport,
    YaesuLegacyDriver,
    YaesuNewcatDriver,
)

PLATFORMS = [Platform.SENSOR]

type HamlibCatConfigEntry = ConfigEntry[HamlibCatCoordinator]


def build_client(data: dict) -> RigClient:
    """Create the transport + protocol driver described by a config entry."""
    if data[CONF_CONNECTION] == CONNECTION_RIGCTLD:
        return RigClient(TcpTransport(data[CONF_HOST], int(data[CONF_PORT])), RigctldDriver())

    protocol = data[CONF_PROTOCOL]
    driver: Driver
    if protocol == PROTOCOL_KENWOOD:
        driver = KenwoodDriver()
    elif protocol == PROTOCOL_YAESU_NEWCAT:
        driver = YaesuNewcatDriver()
    elif protocol == PROTOCOL_YAESU_LEGACY:
        driver = YaesuLegacyDriver()
    elif protocol == PROTOCOL_ICOM_CIV:
        driver = IcomCivDriver(int(data.get(CONF_CIV_ADDRESS, DEFAULT_CIV_ADDRESS), 16))
    else:
        raise ValueError(f"Unknown protocol {protocol}")

    # FT-817/857/897 use 8N2; everything else 8N1.
    stopbits = 2 if protocol == PROTOCOL_YAESU_LEGACY else 1
    return RigClient(
        SerialTransport(
            data[CONF_SERIAL_PORT],
            int(data.get(CONF_BAUDRATE, DEFAULT_BAUDRATE)),
            stopbits,
        ),
        driver,
    )


async def async_setup_entry(hass: HomeAssistant, entry: HamlibCatConfigEntry) -> bool:
    coordinator = HamlibCatCoordinator(hass, entry, build_client(dict(entry.data)))
    # Raises ConfigEntryNotReady (and HA retries) if, for example, the AUX-2's
    # ESPHome entry hasn't connected yet after a restart.
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HamlibCatConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.async_close()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: HamlibCatConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
