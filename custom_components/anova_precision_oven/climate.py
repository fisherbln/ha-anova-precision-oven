"""Climate platform for Anova Precision Oven."""
from __future__ import annotations

import logging
from typing import Any
import uuid

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.unit_conversion import TemperatureConverter

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
    ATTR_STOP_WHEN_DONE,
    ATTR_TITLE,
    ATTR_VENT_OPEN,
    ATTR_WAIT_FOR_START,
    CMD_APO_START,
    CMD_APO_STOP,
    COOK_MODE_DRY,
    DEFAULT_FAN_SPEED,
    DEFAULT_HUMIDITY,
    DEFAULT_TEMPERATURE_CELSIUS,
    OVEN_TYPE_V1,
    OVEN_TYPE_V2,
    TEMP_MAX_CELSIUS,
    TEMP_MIN_CELSIUS,
)
from .cook import (
    CookValidationError,
    build_cook_stages,
    build_start_payload,
    prepare_custom_stages,
    retarget_stages,
)
from .coordinator import AnovaOvenConfigEntry, AnovaOvenCoordinator
from .entity import AnovaOvenEntity

_LOGGER = logging.getLogger(__name__)

HVAC_MODE_MAPPING = {
    "idle": HVACMode.OFF,
    "cook": HVACMode.HEAT,
    "preheat": HVACMode.HEAT,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnovaOvenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Anova Oven climate based on a config entry."""
    coordinator = entry.runtime_data.coordinator

    known_devices: set[str] = set()

    def create_entities_for_device(device_id: str) -> None:
        if device_id in known_devices:
            return

        known_devices.add(device_id)
        async_add_entities([AnovaOvenClimate(coordinator, device_id)])
        _LOGGER.debug("Created climate entity for device %s", device_id)

    for device_id in coordinator.devices:
        create_entities_for_device(device_id)

    @callback
    def handle_coordinator_update() -> None:
        for device_id in coordinator.devices:
            create_entities_for_device(device_id)

    entry.async_on_unload(coordinator.async_add_listener(handle_coordinator_update))


class AnovaOvenClimate(AnovaOvenEntity, ClimateEntity):
    """Anova Oven climate control."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_min_temp = TEMP_MIN_CELSIUS
    _attr_max_temp = TEMP_MAX_CELSIUS
    _attr_target_temperature_step = 5

    def __init__(
        self,
        coordinator: AnovaOvenCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_climate"
        # Target chosen while the oven is off; used when it's turned on
        self._pending_target: float | None = None
        self._attr_name = None

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        state = self.coordinator.get_device_state(self._device_id)
        if not state:
            return HVACMode.OFF

        mode = state.get("state", {}).get("mode", "idle")
        return HVAC_MODE_MAPPING.get(mode, HVACMode.OFF)

    @property
    def hvac_action(self) -> HVACAction:
        """Return current HVAC action."""
        state = self.coordinator.get_device_state(self._device_id)
        if not state:
            return HVACAction.OFF

        mode = state.get("state", {}).get("mode", "idle")
        if mode == "preheat":
            return HVACAction.PREHEATING
        if mode == "cook":
            return HVACAction.HEATING
        return HVACAction.OFF

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        state = self.coordinator.get_device_state(self._device_id)
        if not state:
            return None

        bulbs = state.get("nodes", {}).get("temperatureBulbs", {})
        return bulbs.get(bulbs.get("mode", "dry"), {}).get("current", {}).get("celsius")

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        state = self.coordinator.get_device_state(self._device_id)
        if not state:
            return None

        if self.hvac_mode == HVACMode.OFF and self._pending_target is not None:
            return self._pending_target
        bulbs = state.get("nodes", {}).get("temperatureBulbs", {})
        return bulbs.get(bulbs.get("mode", "dry"), {}).get("setpoint", {}).get("celsius")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        state = self.coordinator.get_device_state(self._device_id)
        if not state:
            return {}

        nodes = state.get("nodes", {})
        temp_bulbs = nodes.get("temperatureBulbs", {})
        timer = nodes.get("timer", {})
        steam = nodes.get("steamGenerators", {})

        attrs = {
            "temperature_mode": temp_bulbs.get("mode"),
            "timer_mode": timer.get("mode"),
            "timer_current": timer.get("current"),
            "timer_initial": timer.get("initial"),
            "humidity": steam.get("relativeHumidity", {}).get("current"),
        }

        if temp_bulbs.get("mode") == "wet":
            wet = temp_bulbs.get("wet", {})
            attrs["wet_temperature_celsius"] = wet.get("current", {}).get("celsius")

        return attrs

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self._async_turn_off()
        elif hvac_mode == HVACMode.HEAT:
            await self._async_turn_on()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        if not isinstance(temperature, (int, float)):
            _LOGGER.error("Invalid temperature type: %s", type(temperature).__name__)
            return

        if temperature < TEMP_MIN_CELSIUS or temperature > TEMP_MAX_CELSIUS:
            _LOGGER.error(
                "Temperature %s°C out of range (%s-%s°C)",
                temperature,
                TEMP_MIN_CELSIUS,
                TEMP_MAX_CELSIUS,
            )
            return

        if self.hvac_mode == HVACMode.HEAT:
            await self._async_adjust_running_cook(temperature)
        else:
            self._pending_target = temperature
            self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Turn on the oven."""
        await self._async_turn_on()

    async def async_turn_off(self) -> None:
        """Turn off the oven."""
        await self._async_turn_off()

    async def _async_turn_on(self) -> None:
        """Start cooking with current or default temperature."""
        target_temp = self.target_temperature
        if target_temp is None:
            target_temp = DEFAULT_TEMPERATURE_CELSIUS

        await self._async_start_cook(target_temp)
        self._pending_target = None

    async def _async_turn_off(self) -> None:
        """Stop cooking."""
        await self.coordinator.send_command(self._device_id, CMD_APO_STOP)
        await self.coordinator.async_request_refresh()

    async def _async_start_cook(self, temperature: float) -> None:
        """Start cooking with specified temperature.

        Note: this always starts a fresh single-stage dry-mode cook with
        default fan speed, humidity, and top+rear heating. It will
        overwrite any in-progress multi-stage or steam cook started from
        the Anova app.
        """
        device_info = self.coordinator.get_device_info(self._device_id)
        device_type = (
            device_info.get("type", OVEN_TYPE_V1) if device_info else OVEN_TYPE_V1
        )

        payload = self._create_cook_payload(temperature, device_type)

        self.coordinator.stop_when_timer_done.discard(self._device_id)
        await self.coordinator.send_command(self._device_id, CMD_APO_START, payload)
        await self.coordinator.async_request_refresh()

    def _create_cook_payload(
        self, temperature: float, device_type: str
    ) -> dict[str, Any]:
        """Create a cook command payload."""
        cook_id = str(uuid.uuid4())
        stage_id = str(uuid.uuid4())

        temperature = max(TEMP_MIN_CELSIUS, min(TEMP_MAX_CELSIUS, temperature))
        fahrenheit = round(temperature * 9 / 5 + 32)

        payload: dict[str, Any] = {
            "stages": [
                {
                    "id": stage_id,
                    "type": "cook",
                    "title": "Cook",
                    "description": "Home Assistant Cook",
                    "userActionRequired": False,
                    "temperatureBulbs": {
                        "mode": "dry",
                        "dry": {
                            "setpoint": {
                                "celsius": temperature,
                                "fahrenheit": fahrenheit,
                            }
                        },
                    },
                    "heatingElements": {
                        "top": {"on": True},
                        "bottom": {"on": False},
                        "rear": {"on": True},
                    },
                    "fan": {"speed": DEFAULT_FAN_SPEED},
                    "vent": {"open": False},
                    "steamGenerators": {
                        "mode": "relative-humidity",
                        "relativeHumidity": {"setpoint": DEFAULT_HUMIDITY},
                    },
                }
            ],
            "cookId": cook_id,
            "originSource": "api",
        }

        if device_type == OVEN_TYPE_V2:
            payload["type"] = "v2"
            payload["cookerId"] = self._device_id

        return payload

    async def _async_adjust_running_cook(self, temperature: float) -> None:
        """Change the running cook's temperature, keeping its other settings."""
        state = self.coordinator.get_device_state(self._device_id) or {}
        cook = state.get("cook") if isinstance(state.get("cook"), dict) else {}
        stages = cook.get("stages") if isinstance(cook.get("stages"), list) else []
        timer = state.get("nodes", {}).get("timer", {})
        remaining_s = timer.get("current") if timer.get("mode") == "running" else None
        try:
            new_stages = retarget_stages(
                stages, cook.get("activeStageId"), temperature, remaining_s
            )
        except CookValidationError as err:
            raise ServiceValidationError(str(err)) from err
        if new_stages is None:
            # Nothing to preserve (e.g. no stage info yet): simple cook
            await self._async_start_cook(temperature)
            return
        keep_stop = self._device_id in self.coordinator.stop_when_timer_done
        await self._async_send_stages(new_stages)
        if keep_stop:
            self.coordinator.stop_when_timer_done.add(self._device_id)

    # --- Entity services -------------------------------------------------

    def _device_type(self) -> str:
        device_info = self.coordinator.get_device_info(self._device_id)
        return device_info.get("type", OVEN_TYPE_V1) if device_info else OVEN_TYPE_V1

    def _to_celsius(self, value: float | None) -> float | None:
        """Convert a service temperature (in HA's unit system) to Celsius."""
        if value is None:
            return None
        return TemperatureConverter.convert(
            value, self.hass.config.units.temperature_unit, UnitOfTemperature.CELSIUS
        )

    async def _async_send_stages(self, stages: list[dict[str, Any]]) -> None:
        # A new cook replaces any stop-after-timer request from the last one
        self.coordinator.stop_when_timer_done.discard(self._device_id)
        payload = build_start_payload(stages, self._device_id, self._device_type())
        await self.coordinator.send_command(self._device_id, CMD_APO_START, payload)
        await self.coordinator.async_request_refresh()

    async def async_start_cook(self, **kwargs: Any) -> None:
        """Start a cook with full control over the oven's settings."""
        if self._device_type() == OVEN_TYPE_V2:
            raise ServiceValidationError(
                "start_cook builds v1 stages; on an Anova Precision Oven v2 use "
                "start_custom_cook with v2 stages"
            )
        duration = kwargs.get(ATTR_DURATION)
        try:
            stages = build_cook_stages(
                temperature_c=self._to_celsius(kwargs[ATTR_TEMPERATURE]),
                mode=kwargs.get(ATTR_MODE, COOK_MODE_DRY),
                heating_elements=kwargs.get(ATTR_HEATING_ELEMENTS, ["rear"]),
                fan_speed=kwargs.get(ATTR_FAN_SPEED, 100),
                steam=kwargs.get(ATTR_STEAM),
                vent_open=kwargs.get(ATTR_VENT_OPEN, False),
                rack_position=int(kwargs.get(ATTR_RACK_POSITION, 3)),
                probe_temperature_c=self._to_celsius(
                    kwargs.get(ATTR_PROBE_TEMPERATURE)
                ),
                duration_s=int(duration.total_seconds()) if duration else None,
                preheat=kwargs.get(ATTR_PREHEAT, True),
                wait_for_start=kwargs.get(ATTR_WAIT_FOR_START, False),
                title=kwargs.get(ATTR_TITLE, ""),
            )
        except CookValidationError as err:
            raise ServiceValidationError(str(err)) from err
        await self._async_send_stages(stages)
        if duration and kwargs.get(ATTR_STOP_WHEN_DONE, False):
            self.coordinator.stop_when_timer_done.add(self._device_id)

    async def async_start_custom_cook(self, **kwargs: Any) -> None:
        """Start a cook from raw stages (e.g. copied from the cook session sensor)."""
        try:
            stages = prepare_custom_stages(kwargs[ATTR_STAGES])
        except CookValidationError as err:
            raise ServiceValidationError(str(err)) from err
        await self._async_send_stages(stages)
