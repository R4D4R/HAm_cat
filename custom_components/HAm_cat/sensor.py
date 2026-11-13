"""Sensors: frequency, mode and band."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfFrequency
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HamlibCatConfigEntry
from .const import CONF_PROTOCOL, DOMAIN
from .coordinator import HamlibCatCoordinator
from .rig import RigState


@dataclass(frozen=True, kw_only=True)
class HamlibSensorDescription(SensorEntityDescription):
    value_fn: Callable[[RigState], Any]
    attrs_fn: Callable[[RigState], dict[str, Any]] | None = None


SENSORS: tuple[HamlibSensorDescription, ...] = (
    HamlibSensorDescription(
        key="frequency",
        translation_key="frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.MEGAHERTZ,
        suggested_display_precision=6,  # 14.074000 MHz (1 Hz resolution)
        value_fn=lambda s: None if s.frequency_hz is None else s.frequency_hz / 1_000_000,
        attrs_fn=lambda s: {"frequency_hz": s.frequency_hz},
    ),
    HamlibSensorDescription(
        key="mode",
        translation_key="mode",
        value_fn=lambda s: s.mode,
        attrs_fn=lambda s: {"passband_hz": s.passband_hz} if s.passband_hz is not None else {},
    ),
    HamlibSensorDescription(
        key="band",
        translation_key="band",
        value_fn=lambda s: s.band,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HamlibCatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(HamlibSensor(coordinator, entry, d) for d in SENSORS)


class HamlibSensor(CoordinatorEntity[HamlibCatCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: HamlibSensorDescription

    def __init__(
        self,
        coordinator: HamlibCatCoordinator,
        entry: HamlibCatConfigEntry,
        description: HamlibSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Amateur radio",
            model=entry.data.get(CONF_PROTOCOL, "").replace("_", " ").title() + " CAT",
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
