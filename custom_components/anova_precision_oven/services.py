"""Actions (services) for the Anova Precision Oven integration."""
from __future__ import annotations

import json
from typing import Any

import voluptuous as vol

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, service

from .const import (
    ATTR_DURATION,
    ATTR_FAN_SPEED,
    ATTR_HEATING_ELEMENTS,
    ATTR_MODE,
    ATTR_PREHEAT,
    ATTR_PROBE_TEMPERATURE,
    ATTR_RACK_POSITION,
    ATTR_STAGES,
    ATTR_STEAM,
    ATTR_TITLE,
    ATTR_VENT_OPEN,
    ATTR_WAIT_FOR_START,
    COOK_MODE_DRY,
    COOK_MODE_SOUS_VIDE,
    DOMAIN,
    HEATING_ELEMENTS,
    RACK_POSITIONS,
    SERVICE_START_COOK,
    SERVICE_START_CUSTOM_COOK,
)


def _stages(value: Any) -> list[dict[str, Any]]:
    """Accept stages as a list or as the JSON text of one."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as err:
            raise vol.Invalid(f"stages is not valid JSON: {err}") from err
    if not isinstance(value, list) or not all(isinstance(s, dict) for s in value):
        raise vol.Invalid("stages must be a list of stage mappings")
    return value


START_COOK_SCHEMA = {
    vol.Required(ATTR_TEMPERATURE): vol.Coerce(float),
    vol.Optional(ATTR_MODE, default=COOK_MODE_DRY): vol.In(
        [COOK_MODE_DRY, COOK_MODE_SOUS_VIDE]
    ),
    vol.Optional(ATTR_HEATING_ELEMENTS, default=["rear"]): vol.All(
        cv.ensure_list, [vol.In(HEATING_ELEMENTS)]
    ),
    vol.Optional(ATTR_FAN_SPEED, default=100): vol.All(
        vol.Coerce(int), vol.Range(min=0, max=100)
    ),
    vol.Optional(ATTR_STEAM): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
    vol.Optional(ATTR_VENT_OPEN, default=False): cv.boolean,
    vol.Optional(ATTR_RACK_POSITION, default=3): vol.All(
        vol.Coerce(int), vol.In(RACK_POSITIONS)
    ),
    vol.Exclusive(ATTR_PROBE_TEMPERATURE, "target"): vol.Coerce(float),
    vol.Exclusive(ATTR_DURATION, "target"): cv.positive_time_period,
    vol.Optional(ATTR_PREHEAT, default=True): cv.boolean,
    vol.Optional(ATTR_WAIT_FOR_START, default=False): cv.boolean,
    vol.Optional(ATTR_TITLE, default=""): cv.string,
}

START_CUSTOM_COOK_SCHEMA = {
    vol.Required(ATTR_STAGES): _stages,
}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions on the oven's climate entity."""
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_START_COOK,
        entity_domain=CLIMATE_DOMAIN,
        schema=START_COOK_SCHEMA,
        func="async_start_cook",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_START_CUSTOM_COOK,
        entity_domain=CLIMATE_DOMAIN,
        schema=START_CUSTOM_COOK_SCHEMA,
        func="async_start_custom_cook",
    )
