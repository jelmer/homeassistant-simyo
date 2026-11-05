"""Sensors for verified aggregate app data; raw account payloads stay private."""

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
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SimyoCoordinator


def _walk(data: Any, path: tuple[str, ...]) -> Any:
    """Walk ``data`` along ``path``; return None if any step is missing."""
    cur: Any = data
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return None
    return cur


def _first_match(data: Any, candidates: tuple[tuple[str, ...], ...]) -> Any:
    for path in candidates:
        value = _walk(data, path)
        if value is not None:
            return value
    return None


@dataclass(frozen=True, kw_only=True)
class SimyoSensorDescription(SensorEntityDescription):
    """Describes a Simyo sensor and how to extract its value."""

    section: str
    value_fn: Callable[[Any], Any]


SENSORS: tuple[SimyoSensorDescription, ...] = (
    SimyoSensorDescription(
        key="balance",
        translation_key="balance",
        section="balance",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: _first_match(
            d,
            (
                ("result", "balance"),
                ("result", "creditBalance", "amount"),
                ("balance",),
                ("result", "credit"),
                ("credit",),
            ),
        ),
    ),
    SimyoSensorDescription(
        key="data_used",
        translation_key="data_used",
        section="usage",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: _first_match(
            d,
            (
                ("result", "data", "used"),
                ("data", "used"),
                ("result", "usage", "data", "used"),
                ("usage", "data", "used"),
            ),
        ),
    ),
    SimyoSensorDescription(
        key="data_remaining",
        translation_key="data_remaining",
        section="usage",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _first_match(
            d,
            (
                ("result", "data", "remaining"),
                ("data", "remaining"),
                ("result", "usage", "data", "remaining"),
            ),
        ),
    ),
    SimyoSensorDescription(
        key="voice_used",
        translation_key="voice_used",
        section="usage",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: _first_match(
            d,
            (
                ("result", "voice", "used"),
                ("voice", "used"),
                ("result", "calls", "used"),
                ("calls", "used"),
            ),
        ),
    ),
    SimyoSensorDescription(
        key="voice_remaining",
        translation_key="voice_remaining",
        section="usage",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _first_match(
            d,
            (
                ("result", "voice", "remaining"),
                ("voice", "remaining"),
                ("result", "calls", "remaining"),
            ),
        ),
    ),
    SimyoSensorDescription(
        key="sms_used",
        translation_key="sms_used",
        section="usage",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: _first_match(
            d,
            (
                ("result", "sms", "used"),
                ("sms", "used"),
                ("result", "texts", "used"),
            ),
        ),
    ),
    SimyoSensorDescription(
        key="sms_remaining",
        translation_key="sms_remaining",
        section="usage",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _first_match(
            d,
            (
                ("result", "sms", "remaining"),
                ("sms", "remaining"),
                ("result", "texts", "remaining"),
            ),
        ),
    ),
    SimyoSensorDescription(
        key="out_of_bundle_costs",
        translation_key="out_of_bundle_costs",
        section="usage",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: _first_match(
            d,
            (
                ("result", "outOfBundle", "costs"),
                ("outOfBundle", "costs"),
                ("result", "extra", "costs"),
                ("extra", "costs"),
            ),
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SimyoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SimyoSensor(coordinator, entry, description) for description in SENSORS
    )


class SimyoSensor(CoordinatorEntity[SimyoCoordinator], SensorEntity):
    """A single Simyo sensor."""

    entity_description: SimyoSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SimyoCoordinator,
        entry: ConfigEntry,
        description: SimyoSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        phone_number = coordinator.client.phone_number
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Simyo {phone_number}",
            manufacturer="Simyo",
        )

    @property
    def native_value(self) -> Any:
        section = self.coordinator.data.get(self.entity_description.section)
        if section is None:
            return None
        return self.entity_description.value_fn(section)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Never publish account details or tokens from an app response."""
        return None
