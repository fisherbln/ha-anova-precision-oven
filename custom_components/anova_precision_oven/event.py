"""Event platform: cook milestones (preheated, probe target, timer, cook ended)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    EVENT_COOK_ENDED,
    EVENT_PREHEATED,
    EVENT_PROBE_TARGET_REACHED,
    EVENT_TIMER_FINISHED,
    EVENT_TYPES,
    PREHEAT_TOLERANCE_CELSIUS,
)
from .coordinator import AnovaOvenConfigEntry, AnovaOvenCoordinator
from .entity import AnovaOvenEntity

_LOGGER = logging.getLogger(__name__)


def _get(state: dict[str, Any], *keys: str) -> Any:
    current: Any = state
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def cook_milestones(state: dict[str, Any]) -> dict[str, Any]:
    """Return which milestones the current state has reached.

    Keys are event types whose condition holds right now, mapped to the
    attributes to send with the event.
    """
    reached: dict[str, Any] = {}
    mode = _get(state, "state", "mode")
    if not mode or mode == "idle":
        return reached

    bulb = _get(state, "nodes", "temperatureBulbs", "mode") or "dry"
    current = _get(state, "nodes", "temperatureBulbs", bulb, "current", "celsius")
    setpoint = _get(state, "nodes", "temperatureBulbs", bulb, "setpoint", "celsius")
    cook = state.get("cook") if isinstance(state.get("cook"), dict) else {}
    stages = cook.get("stages") if isinstance(cook.get("stages"), list) else []
    active = next(
        (s for s in stages if isinstance(s, dict) and s.get("id") == cook.get("activeStageId")),
        None,
    )
    has_preheat = any(isinstance(s, dict) and s.get("type") == "preheat" for s in stages)
    if has_preheat:
        # The oven decides when preheating is done: it moves to the next stage.
        preheated = active is not None and active.get("type") != "preheat"
    else:
        preheated = (
            current is not None
            and setpoint is not None
            and current >= setpoint - PREHEAT_TOLERANCE_CELSIUS
        )
    if preheated:
        reached[EVENT_PREHEATED] = {
            "temperature_mode": bulb,
            "setpoint_celsius": setpoint,
        }

    probe = _get(state, "nodes", "temperatureProbe") or {}
    probe_now = _get(probe, "current", "celsius")
    probe_target = _get(probe, "setpoint", "celsius")
    if probe.get("connected") and probe_now is not None and probe_target is not None:
        if probe_now >= probe_target:
            reached[EVENT_PROBE_TARGET_REACHED] = {
                "probe_celsius": probe_now,
                "setpoint_celsius": probe_target,
            }

    # timer_finished is tracked by the coordinator (it needs the previous
    # state); see AnovaOvenCoordinator._track_timer.

    return reached


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnovaOvenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the cook event entity."""
    coordinator = entry.runtime_data.coordinator
    known_devices: set[str] = set()

    @callback
    def handle_coordinator_update() -> None:
        new = [d for d in coordinator.devices if d not in known_devices]
        known_devices.update(new)
        if new:
            async_add_entities([AnovaOvenCookEvent(coordinator, d) for d in new])

    handle_coordinator_update()
    entry.async_on_unload(coordinator.async_add_listener(handle_coordinator_update))


class AnovaOvenCookEvent(AnovaOvenEntity, EventEntity):
    """Fires once per cook when a milestone is reached."""

    _attr_event_types = EVENT_TYPES
    _attr_icon = "mdi:chef-hat"

    def __init__(self, coordinator: AnovaOvenCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_cook_event"
        self._attr_name = "Cook"
        self._primed = False
        self._cooking = False
        self._fired: set[str] = set()
        self._timers_finished = 0

    @callback
    def _handle_coordinator_update(self) -> None:
        state = self.coordinator.get_device_state(self._device_id)
        if state:
            self._process(state)
            finished = (self.coordinator.devices.get(self._device_id) or {}).get(
                "timer_finished_count", 0
            )
            if finished > self._timers_finished and self._primed:
                self._fire(EVENT_TIMER_FINISHED, {})
            self._timers_finished = finished
        super()._handle_coordinator_update()

    def _process(self, state: dict[str, Any]) -> None:
        mode = _get(state, "state", "mode")
        cooking = bool(mode) and mode != "idle"
        reached = cook_milestones(state)

        if not self._primed:
            # First state after startup: note what has already happened so a
            # restart mid-cook doesn't re-fire old milestones.
            self._primed = True
            self._cooking = cooking
            self._fired = set(reached)
            return

        if cooking and not self._cooking:
            self._fired = set()
        if not cooking and self._cooking:
            self._fire(EVENT_COOK_ENDED, {})
            self._fired = set()
        self._cooking = cooking

        for event_type, attributes in reached.items():
            if event_type not in self._fired:
                self._fired.add(event_type)
                self._fire(event_type, attributes)

    def _fire(self, event_type: str, attributes: dict[str, Any]) -> None:
        _LOGGER.debug("Cook event %s %s", event_type, attributes)
        self._trigger_event(event_type, attributes)
