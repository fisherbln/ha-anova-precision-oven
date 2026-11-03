"""Switch platform for Anova Precision Oven."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CMD_APO_SET_LAMP
from .coordinator import AnovaOvenConfigEntry, AnovaOvenCoordinator
from .entity import AnovaOvenEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnovaOvenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Anova Oven switches based on a config entry."""
    coordinator = entry.runtime_data.coordinator

    known_devices: set[str] = set()

    def create_entities_for_device(device_id: str) -> None:
        if device_id in known_devices:
            return

        known_devices.add(device_id)
        async_add_entities([AnovaOvenLampSwitch(coordinator, device_id)])
        _LOGGER.debug("Created switch entities for device %s", device_id)

    for device_id in coordinator.devices:
        create_entities_for_device(device_id)

    @callback
    def handle_coordinator_update() -> None:
        for device_id in coordinator.devices:
            create_entities_for_device(device_id)

    entry.async_on_unload(coordinator.async_add_listener(handle_coordinator_update))


class AnovaOvenLampSwitch(AnovaOvenEntity, SwitchEntity):
    """Oven lamp switch."""

    def __init__(
        self,
        coordinator: AnovaOvenCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the lamp switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_lamp"
        self._attr_name = "Lamp"
        self._attr_icon = "mdi:lightbulb"

    @property
    def is_on(self) -> bool | None:
        """Return true if the lamp is on."""
        state = self.coordinator.get_device_state(self._device_id)
        if not state:
            return None

        return state.get("nodes", {}).get("lamp", {}).get("on", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the lamp."""
        await self.coordinator.send_command(
            self._device_id, CMD_APO_SET_LAMP, {"on": True}
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the lamp."""
        await self.coordinator.send_command(
            self._device_id, CMD_APO_SET_LAMP, {"on": False}
        )
        await self.coordinator.async_request_refresh()
