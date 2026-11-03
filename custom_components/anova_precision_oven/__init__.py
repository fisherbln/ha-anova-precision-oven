"""The Anova Precision Oven integration."""
from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CONF_TOKEN, DOMAIN
from .coordinator import (
    AnovaOvenConfigEntry,
    AnovaOvenCoordinator,
    AnovaOvenRuntimeData,
)
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.EVENT,
    Platform.SWITCH,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AnovaOvenConfigEntry) -> bool:
    """Set up Anova Precision Oven from a config entry."""
    coordinator = AnovaOvenCoordinator(hass, entry.data[CONF_TOKEN])
    entry.runtime_data = AnovaOvenRuntimeData(coordinator=coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if hass.is_running:
        hass.async_create_background_task(
            coordinator.start_websocket(),
            "anova_precision_oven_websocket",
        )
    else:
        async def _start_websocket(_event):
            hass.async_create_background_task(
                coordinator.start_websocket(),
                "anova_precision_oven_websocket",
            )

        entry.async_on_unload(
            hass.bus.async_listen_once("homeassistant_started", _start_websocket)
        )

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AnovaOvenConfigEntry
) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.coordinator.stop_websocket()

    return unload_ok
