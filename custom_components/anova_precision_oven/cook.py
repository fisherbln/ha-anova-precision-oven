"""Build CMD_APO_START payloads for the Anova Precision Oven.

Pure functions (no Home Assistant state) so they can be unit tested.
Field names and limits follow Anova's v1 developer docs and reference
client (github.com/anova-culinary/developer-project-wifi).
"""
from __future__ import annotations

from collections.abc import Iterable
import copy
from typing import Any
import uuid

from .const import (
    COOK_MODE_SOUS_VIDE,
    HEATING_ELEMENTS,
    OVEN_TYPE_V2,
    PROBE_MAX_CELSIUS,
    PROBE_MIN_CELSIUS,
    TEMP_MAX_BOTTOM_ONLY_CELSIUS,
    TEMP_MAX_CELSIUS,
    TEMP_MAX_WET_CELSIUS,
    TEMP_MIN_CELSIUS,
)


class CookValidationError(ValueError):
    """Raised when requested cook settings are outside what the oven accepts."""


def setpoint(celsius: float) -> dict[str, float]:
    """Return a setpoint with both units (v1 requires both)."""
    return {
        "celsius": round(celsius, 2),
        "fahrenheit": round(celsius * 9 / 5 + 32, 2),
    }


def build_cook_stages(
    *,
    temperature_c: float,
    mode: str = "dry",
    heating_elements: Iterable[str] = ("rear",),
    fan_speed: int = 100,
    steam: int | None = None,
    vent_open: bool = False,
    rack_position: int = 3,
    probe_temperature_c: float | None = None,
    duration_s: int | None = None,
    preheat: bool = True,
    wait_for_start: bool = False,
    title: str = "",
) -> list[dict[str, Any]]:
    """Build the stage list for a single cook (optionally preceded by a preheat).

    - mode "dry" heats on the dry bulb; "sous_vide" holds the wet bulb.
    - steam is a percentage: steam-percentage in dry mode, relative
      humidity in sous vide mode (defaults to 100 there, as in the app).
    - preheat adds a preheat stage, so the cook stage (and its timer)
      only starts once the oven is up to temperature.
    - wait_for_start makes the oven wait for a tap on the oven after
      preheating before it starts the cook stage.
    - fan_speed only applies with top/bottom elements and no steam; the rear
      element or any steam locks the fan at 100, as in the Anova app.
    """
    elements = {e.lower() for e in heating_elements}
    if unknown := elements - set(HEATING_ELEMENTS):
        raise CookValidationError(f"Unknown heating element(s): {sorted(unknown)}")
    if not elements:
        raise CookValidationError("At least one heating element must be on")

    wet = mode == COOK_MODE_SOUS_VIDE
    max_c = TEMP_MAX_WET_CELSIUS if wet else TEMP_MAX_CELSIUS
    if not wet and elements == {"bottom"}:
        max_c = TEMP_MAX_BOTTOM_ONLY_CELSIUS
    if not TEMP_MIN_CELSIUS <= temperature_c <= max_c:
        raise CookValidationError(
            f"Temperature must be between {TEMP_MIN_CELSIUS} and {max_c} °C "
            "for these settings"
        )

    if probe_temperature_c is not None and duration_s is not None:
        raise CookValidationError("Use either a probe target or a timer, not both")
    if probe_temperature_c is not None and not (
        PROBE_MIN_CELSIUS <= probe_temperature_c <= PROBE_MAX_CELSIUS
    ):
        raise CookValidationError(
            f"Probe target must be between {PROBE_MIN_CELSIUS} and "
            f"{PROBE_MAX_CELSIUS} °C"
        )
    if duration_s is not None and duration_s <= 0:
        raise CookValidationError("Timer must be longer than zero")
    if wait_for_start and not preheat:
        raise CookValidationError("wait_for_start needs preheat to be on")
    if not 0 <= fan_speed <= 100:
        raise CookValidationError("Fan speed must be 0-100")
    if steam is not None and not 0 <= steam <= 100:
        raise CookValidationError("Steam must be 0-100")

    # Like the Anova app: the fan is locked on high with the rear element or
    # any steam (sous vide always uses the steam generators).
    if "rear" in elements or wet or steam:
        fan_speed = 100

    bulb = "wet" if wet else "dry"
    base: dict[str, Any] = {
        "stepType": "stage",
        "title": title,
        "description": "",
        "userActionRequired": False,
        "temperatureBulbs": {
            "mode": bulb,
            bulb: {"setpoint": setpoint(temperature_c)},
        },
        "heatingElements": {e: {"on": e in elements} for e in HEATING_ELEMENTS},
        "fan": {"speed": fan_speed},
        "vent": {"open": vent_open},
        "rackPosition": rack_position,
        "stageTransitionType": "automatic",
    }

    if wet:
        base["steamGenerators"] = {
            "mode": "relative-humidity",
            "relativeHumidity": {"setpoint": 100 if steam is None else steam},
        }
    elif steam:
        base["steamGenerators"] = {
            "mode": "steam-percentage",
            "steamPercentage": {"setpoint": steam},
        }

    if probe_temperature_c is not None:
        base["probeAdded"] = True
        base["temperatureProbe"] = {"setpoint": setpoint(probe_temperature_c)}

    stages: list[dict[str, Any]] = []
    if preheat:
        stage = copy.deepcopy(base)
        stage["id"] = str(uuid.uuid4())
        stage["type"] = "preheat"
        stages.append(stage)

    cook = copy.deepcopy(base)
    cook["id"] = str(uuid.uuid4())
    cook["type"] = "cook"
    cook["userActionRequired"] = wait_for_start
    if duration_s is not None:
        cook["timerAdded"] = True
        cook["timer"] = {"initial": int(duration_s)}
    stages.append(cook)
    return stages


def retarget_stages(
    stages: list[dict[str, Any]],
    active_stage_id: str | None,
    temperature_c: float,
    timer_remaining_s: int | None = None,
) -> list[dict[str, Any]] | None:
    """Return the rest of a running cook with a new temperature.

    The oven has no "adjust" command, so a change is sent as a new cook made
    of the active stage and the stages after it. The active stage gets the
    new temperature, and so do later stages that shared its old setpoint
    (a preheat and its cook stage, say); stages with their own temperatures
    keep them. A running timer carries over its remaining time. Returns None
    when the active stage can't be found.
    """
    index = next(
        (
            i
            for i, stage in enumerate(stages)
            if isinstance(stage, dict) and stage.get("id") == active_stage_id
        ),
        None,
    )
    if index is None:
        return None

    remaining = copy.deepcopy(stages[index:])

    def bulb(stage: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        bulbs = stage.setdefault("temperatureBulbs", {"mode": "dry"})
        mode = bulbs.get("mode", "dry")
        return mode, bulbs.setdefault(mode, {})

    mode, active_bulb = bulb(remaining[0])
    old = (active_bulb.get("setpoint") or {}).get("celsius")
    max_c = TEMP_MAX_WET_CELSIUS if mode == "wet" else TEMP_MAX_CELSIUS
    if not TEMP_MIN_CELSIUS <= temperature_c <= max_c:
        raise CookValidationError(
            f"Temperature must be between {TEMP_MIN_CELSIUS} and {max_c} °C"
        )

    for position, stage in enumerate(remaining):
        stage_mode, stage_bulb = bulb(stage)
        current = (stage_bulb.get("setpoint") or {}).get("celsius")
        if position == 0 or (stage_mode == mode and current == old):
            stage_bulb["setpoint"] = setpoint(temperature_c)

    if timer_remaining_s and isinstance(remaining[0].get("timer"), dict):
        remaining[0]["timer"]["initial"] = int(timer_remaining_s)
    return remaining


def prepare_custom_stages(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate raw stages (e.g. copied from the cook session sensor).

    Stages are passed to the oven as-is; a stage without an id gets one.
    """
    if not isinstance(stages, list) or not stages:
        raise CookValidationError("stages must be a non-empty list")
    prepared = []
    for stage in stages:
        if not isinstance(stage, dict):
            raise CookValidationError("Each stage must be a mapping")
        stage = copy.deepcopy(stage)
        stage.setdefault("id", str(uuid.uuid4()))
        prepared.append(stage)
    return prepared


def build_start_payload(
    stages: list[dict[str, Any]], device_id: str, device_type: str
) -> dict[str, Any]:
    """Wrap stages in the CMD_APO_START inner payload."""
    payload: dict[str, Any] = {
        "stages": stages,
        "cookId": str(uuid.uuid4()),
        "originSource": "api",
    }
    if device_type == OVEN_TYPE_V2:
        payload["type"] = "v2"
        payload["cookerId"] = device_id
    return payload
