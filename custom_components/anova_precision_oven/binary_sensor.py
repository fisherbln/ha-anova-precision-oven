"""Binary sensor platform for Anova Precision Oven."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AnovaOvenConfigEntry, AnovaOvenCoordinator
from .entity import AnovaOvenEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class AnovaOvenBinarySensorDescription(BinarySensorEntityDescription):
    """Describes an Anova Oven binary sensor."""

    is_on_fn: Callable[[dict[str, Any]], bool | None]
    extra_attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


# Flags the oven reports for hardware faults, as (label, path).
FAULT_FLAGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("triacs_failed", ("systemInfo", "triacsFailed")),
    ("top_dry_bulb_overheated", ("nodes", "temperatureBulbs", "dryTop", "overheated")),
    (
        "bottom_dry_bulb_overheated",
        ("nodes", "temperatureBulbs", "dryBottom", "overheated"),
    ),
    ("wet_bulb_dose_failed", ("nodes", "temperatureBulbs", "wet", "doseFailed")),
    ("evaporator_failed", ("nodes", "steamGenerators", "evaporator", "failed")),
    ("evaporator_overheated", ("nodes", "steamGenerators", "evaporator", "overheated")),
    ("boiler_failed", ("nodes", "steamGenerators", "boiler", "failed")),
    ("boiler_overheated", ("nodes", "steamGenerators", "boiler", "overheated")),
    ("top_element_failed", ("nodes", "heatingElements", "top", "failed")),
    ("bottom_element_failed", ("nodes", "heatingElements", "bottom", "failed")),
    ("rear_element_failed", ("nodes", "heatingElements", "rear", "failed")),
    ("fan_failed", ("nodes", "fan", "failed")),
    ("lamp_failed", ("nodes", "lamp", "failed")),
    ("ui_communication_failed", ("nodes", "userInterfaceCircuit", "communicationFailed")),
)


def _flag(state: dict[str, Any], path: tuple[str, ...]) -> bool:
    current: Any = state
    for key in path:
        if not isinstance(current, dict):
            return False
        current = current.get(key)
    return current is True


def _active_faults(state: dict[str, Any]) -> list[str]:
    return [label for label, path in FAULT_FLAGS if _flag(state, path)]


BINARY_SENSOR_DESCRIPTIONS: tuple[AnovaOvenBinarySensorDescription, ...] = (
    AnovaOvenBinarySensorDescription(
        key="descale_required",
        name="Descale required",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:water-alert",
        is_on_fn=lambda s: _flag(s, ("nodes", "steamGenerators", "boiler", "descaleRequired")),
    ),
    AnovaOvenBinarySensorDescription(
        key="fault",
        name="Fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda s: bool(_active_faults(s)),
        extra_attrs_fn=lambda s: {"faults": _active_faults(s)},
    ),
    AnovaOvenBinarySensorDescription(
        key="connectivity",
        name="Connectivity",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        is_on_fn=lambda s: s.get("systemInfo", {}).get("online", False),
        extra_attrs_fn=lambda s: {
            "last_connected": s.get("systemInfo", {}).get(
                "lastConnectedTimestamp"
            ),
            "last_disconnected": s.get("systemInfo", {}).get(
                "lastDisconnectedTimestamp"
            ),
            "firmware_version": s.get("systemInfo", {}).get("firmwareVersion"),
            "hardware_version": s.get("systemInfo", {}).get("hardwareVersion"),
        },
    ),
    AnovaOvenBinarySensorDescription(
        key="door",
        name="Door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=lambda s: not s.get("nodes", {})
        .get("door", {})
        .get("closed", True),
    ),
    AnovaOvenBinarySensorDescription(
        key="water_tank",
        name="Water tank",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda s: s.get("nodes", {})
        .get("waterTank", {})
        .get("empty", False),
        extra_attrs_fn=lambda s: {
            "descale_required": s.get("nodes", {})
            .get("steamGenerators", {})
            .get("boiler", {})
            .get("descaleRequired", False),
        },
    ),
    AnovaOvenBinarySensorDescription(
        key="vent",
        name="Vent",
        device_class=BinarySensorDeviceClass.OPENING,
        is_on_fn=lambda s: s.get("nodes", {})
        .get("vent", {})
        .get("open", False),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnovaOvenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Anova Oven binary sensors based on a config entry."""
    coordinator = entry.runtime_data.coordinator

    known_devices: set[str] = set()

    def create_entities_for_device(device_id: str) -> None:
        if device_id in known_devices:
            return

        known_devices.add(device_id)
        entities = [
            AnovaOvenBinarySensor(coordinator, device_id, description)
            for description in BINARY_SENSOR_DESCRIPTIONS
        ]
        async_add_entities(entities)
        _LOGGER.debug(
            "Created %d binary sensor entities for device %s",
            len(entities),
            device_id,
        )

    for device_id in coordinator.devices:
        create_entities_for_device(device_id)

    @callback
    def handle_coordinator_update() -> None:
        for device_id in coordinator.devices:
            create_entities_for_device(device_id)

    entry.async_on_unload(coordinator.async_add_listener(handle_coordinator_update))


class AnovaOvenBinarySensor(AnovaOvenEntity, BinarySensorEntity):
    """Binary sensor entity for Anova Precision Oven."""

    entity_description: AnovaOvenBinarySensorDescription

    def __init__(
        self,
        coordinator: AnovaOvenCoordinator,
        device_id: str,
        description: AnovaOvenBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return the state of the binary sensor."""
        state = self.coordinator.get_device_state(self._device_id)
        if not state:
            return None
        return self.entity_description.is_on_fn(state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        if self.entity_description.extra_attrs_fn is None:
            return {}
        state = self.coordinator.get_device_state(self._device_id)
        if not state:
            return {}
        return self.entity_description.extra_attrs_fn(state)
