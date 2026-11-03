"""End-to-end tests against Home Assistant with a fake oven websocket."""
from __future__ import annotations

import pytest
import voluptuous as vol

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.anova_precision_oven.const import DOMAIN

from .conftest import DEVICE_ID, idle_state

CLIMATE = "climate.anova_precision_oven"


async def test_entities_from_real_state(hass, setup_oven):
    assert hass.states.get("binary_sensor.anova_precision_oven_descale_required").state == "on"
    fault = hass.states.get("binary_sensor.anova_precision_oven_fault")
    assert fault.state == "off" and fault.attributes["faults"] == []
    assert hass.states.get("sensor.anova_precision_oven_heating_power").state == "0"
    assert hass.states.get("sensor.anova_precision_oven_steam_mode").state == "idle"
    assert hass.states.get("sensor.anova_precision_oven_cook_session").state == "idle"
    assert hass.states.get("sensor.anova_precision_oven_cook_time").state == "0"
    assert hass.states.get("event.anova_precision_oven_cook") is not None
    # Per-element power sensors exist but are disabled by default
    assert hass.states.get("sensor.anova_precision_oven_top_element_power") is None


async def test_start_cook_sends_v1_payload_in_fahrenheit(hass, setup_oven):
    hass.config.units = US_CUSTOMARY_SYSTEM
    _, _, ws, _ = setup_oven
    await hass.services.async_call(
        DOMAIN,
        "start_cook",
        {
            "entity_id": CLIMATE,
            "temperature": 350,
            "heating_elements": ["top", "rear"],
            "fan_speed": 60,
            "steam": 20,
            "rack_position": 2,
            "duration": {"minutes": 25},
        },
        blocking=True,
    )
    message = ws.sent[-1]
    assert message["command"] == "CMD_APO_START"
    assert message["payload"]["id"] == DEVICE_ID
    stages = message["payload"]["payload"]["stages"]
    assert [s["type"] for s in stages] == ["preheat", "cook"]
    cook = stages[1]
    assert cook["temperatureBulbs"]["dry"]["setpoint"]["fahrenheit"] == 350
    assert cook["temperatureBulbs"]["dry"]["setpoint"]["celsius"] == pytest.approx(176.67)
    assert cook["timer"] == {"initial": 1500}
    assert cook["rackPosition"] == 2
    assert cook["steamGenerators"]["steamPercentage"] == {"setpoint": 20}


async def test_probe_and_timer_are_exclusive(hass, setup_oven):
    with pytest.raises((vol.Invalid, ServiceValidationError)):
        await hass.services.async_call(
            DOMAIN,
            "start_cook",
            {"entity_id": CLIMATE, "temperature": 150, "probe_temperature": 60, "duration": 60},
            blocking=True,
        )


async def test_out_of_range_is_a_validation_error(hass, setup_oven):
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "start_cook",
            {"entity_id": CLIMATE, "temperature": 110, "mode": "sous_vide"},
            blocking=True,
        )


async def test_custom_cook_accepts_json_text(hass, setup_oven):
    _, _, ws, _ = setup_oven
    await hass.services.async_call(
        DOMAIN,
        "start_custom_cook",
        {"entity_id": CLIMATE, "stages": '[{"id": "a", "type": "cook", "stepType": "stage"}]'},
        blocking=True,
    )
    assert ws.sent[-1]["payload"]["payload"]["stages"][0]["id"] == "a"


async def test_rejected_command_raises(hass, setup_oven):
    _, _, ws, _ = setup_oven
    ws.reply = {"status": "error", "error": "Not allowed"}
    with pytest.raises(HomeAssistantError, match="Not allowed"):
        await hass.services.async_call(
            "climate", "turn_off", {"entity_id": CLIMATE}, blocking=True
        )


async def test_cook_events_fire_once(hass, setup_oven):
    _, _, _, push = setup_oven
    event_id = "event.anova_precision_oven_cook"

    cooking = idle_state()
    cooking["state"]["mode"] = "cook"
    cooking["cook"] = {
        "cookId": "c1",
        "activeStageId": "s1",
        "secondsElapsed": 120,
        "stages": [
            {"id": "s1", "type": "preheat", "rackPosition": 4},
            {"id": "s2", "type": "cook", "rackPosition": 4},
        ],
    }
    cooking["nodes"]["temperatureBulbs"]["dry"]["setpoint"] = {"celsius": 180, "fahrenheit": 356}
    cooking["nodes"]["temperatureBulbs"]["dry"]["current"]["celsius"] = 120
    cooking["nodes"]["heatingElements"]["rear"]["watts"] = 1800
    await push(cooking)
    assert hass.states.get(event_id).state == "unknown"
    assert hass.states.get("sensor.anova_precision_oven_heating_power").state == "1800"
    assert hass.states.get("sensor.anova_precision_oven_cook_session").state == "c1"
    assert hass.states.get("sensor.anova_precision_oven_active_stage").state == "1"
    assert hass.states.get("sensor.anova_precision_oven_rack_position").state == "4"
    assert hass.states.get("sensor.anova_precision_oven_cook_time").state == "120"

    # Within 1 °C of the setpoint but the oven is still in its preheat stage:
    # not preheated yet (the oven decides, by moving to the next stage).
    cooking["cook"]["activeStageId"] = "s1"
    cooking["nodes"]["temperatureBulbs"]["dry"]["current"]["celsius"] = 179.5
    cooking["nodes"]["timer"] = {"mode": "idle", "initial": 120, "current": 120}
    await push(cooking)
    assert hass.states.get(event_id).state == "unknown"

    cooking["cook"]["activeStageId"] = "s2"
    cooking["nodes"]["timer"] = {"mode": "running", "initial": 120, "current": 107}
    await push(cooking)
    first = hass.states.get(event_id)
    assert first.attributes["event_type"] == "preheated"

    # Same milestone again: no new event
    await push(cooking)
    assert hass.states.get(event_id).state == first.state

    cooking["nodes"]["temperatureProbe"] = {
        "connected": True,
        "current": {"celsius": 63.2},
        "setpoint": {"celsius": 63},
    }
    await push(cooking)
    assert hass.states.get(event_id).attributes["event_type"] == "probe_target_reached"

    # Real v1 behavior: at zero the timer resets to idle 0/0 and the oven keeps
    # holding temperature.
    cooking["nodes"]["timer"] = {"mode": "running", "initial": 120, "current": 3}
    await push(cooking)
    cooking["nodes"]["timer"] = {"mode": "idle", "initial": 0, "current": 0}
    await push(cooking)
    assert hass.states.get(event_id).attributes["event_type"] == "timer_finished"

    await push(idle_state())
    assert hass.states.get(event_id).attributes["event_type"] == "cook_ended"


async def test_action_definitions_match_schema_and_translations(hass, setup_oven):
    import json
    import pathlib

    from homeassistant.loader import async_get_integration
    from homeassistant.util.yaml import load_yaml_dict

    from custom_components.anova_precision_oven.services import (
        START_COOK_SCHEMA,
        START_CUSTOM_COOK_SCHEMA,
    )

    integration = await async_get_integration(hass, DOMAIN)
    root = pathlib.Path(integration.file_path)
    yaml_defs = await hass.async_add_executor_job(load_yaml_dict, str(root / "services.yaml"))
    translations = json.loads((root / "translations" / "en.json").read_text())["services"]
    schemas = {"start_cook": START_COOK_SCHEMA, "start_custom_cook": START_CUSTOM_COOK_SCHEMA}
    for name, schema in schemas.items():
        schema_fields = {str(key) for key in schema}
        assert set(yaml_defs[name]["fields"]) == schema_fields
        assert set(translations[name]["fields"]) == schema_fields
    assert hass.services.has_service(DOMAIN, "start_cook")
    assert hass.services.has_service(DOMAIN, "start_custom_cook")


async def test_lamp_and_stop_still_work(hass, setup_oven):
    _, _, ws, _ = setup_oven
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.anova_precision_oven_lamp"}, blocking=True
    )
    assert ws.sent[-1]["command"] == "CMD_APO_SET_LAMP"
    await hass.services.async_call("climate", "turn_off", {"entity_id": CLIMATE}, blocking=True)
    assert ws.sent[-1]["command"] == "CMD_APO_STOP"


def _timer_state(timer_mode: str, current: int, initial: int = 120):
    state = idle_state()
    state["state"]["mode"] = "cook"
    state["cook"] = {"cookId": "c1", "activeStageId": "s2", "stages": [{"id": "s2", "type": "cook"}]}
    state["nodes"]["timer"] = {"mode": timer_mode, "initial": initial, "current": current}
    return state


@pytest.mark.parametrize(("stop_when_done", "expect_stop"), [(True, True), (False, False)])
async def test_stop_when_done(hass, setup_oven, stop_when_done, expect_stop):
    _, _, ws, push = setup_oven
    await hass.services.async_call(
        DOMAIN,
        "start_cook",
        {"entity_id": CLIMATE, "temperature": 150, "duration": 120, "stop_when_done": stop_when_done},
        blocking=True,
    )
    sent_before = len(ws.sent)
    await push(_timer_state("idle", 120))
    await push(_timer_state("running", 5))
    assert len(ws.sent) == sent_before
    await push(_timer_state("idle", 0, initial=0))
    await hass.async_block_till_done()
    stops = [m for m in ws.sent[sent_before:] if m["command"] == "CMD_APO_STOP"]
    assert bool(stops) is expect_stop


async def test_stop_when_done_is_off_by_default(hass, setup_oven):
    _, coordinator, _, _ = setup_oven
    await hass.services.async_call(
        DOMAIN, "start_cook", {"entity_id": CLIMATE, "temperature": 150, "duration": 60}, blocking=True
    )
    assert not coordinator.stop_when_timer_done


async def test_new_cook_cancels_pending_stop(hass, setup_oven):
    _, coordinator, _, _ = setup_oven
    await hass.services.async_call(
        DOMAIN,
        "start_cook",
        {"entity_id": CLIMATE, "temperature": 150, "duration": 60, "stop_when_done": True},
        blocking=True,
    )
    assert coordinator.stop_when_timer_done
    await hass.services.async_call(
        DOMAIN, "start_cook", {"entity_id": CLIMATE, "temperature": 150}, blocking=True
    )
    assert not coordinator.stop_when_timer_done
