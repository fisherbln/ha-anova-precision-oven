"""Tests for the cook payload builder."""
import pytest

from custom_components.anova_precision_oven.cook import (
    CookValidationError,
    build_cook_stages,
    build_start_payload,
    prepare_custom_stages,
)


def test_roast_with_preheat_and_timer():
    stages = build_cook_stages(
        temperature_c=176.67,
        heating_elements=["top", "rear"],
        fan_speed=80,
        duration_s=1800,
    )
    assert [s["type"] for s in stages] == ["preheat", "cook"]
    preheat, cook = stages
    assert preheat["id"] != cook["id"]
    assert cook["temperatureBulbs"] == {
        "mode": "dry",
        "dry": {"setpoint": {"celsius": 176.67, "fahrenheit": 350.01}},
    }
    assert cook["heatingElements"] == {
        "top": {"on": True},
        "bottom": {"on": False},
        "rear": {"on": True},
    }
    assert cook["fan"] == {"speed": 80}
    assert cook["stepType"] == "stage"
    assert cook["stageTransitionType"] == "automatic"
    assert cook["timerAdded"] is True and cook["timer"] == {"initial": 1800}
    assert "timer" not in preheat
    assert "steamGenerators" not in cook


def test_sous_vide_defaults_to_full_humidity():
    (cook,) = build_cook_stages(temperature_c=57, mode="sous_vide", preheat=False)
    assert cook["temperatureBulbs"]["mode"] == "wet"
    assert cook["steamGenerators"] == {
        "mode": "relative-humidity",
        "relativeHumidity": {"setpoint": 100},
    }


def test_dry_steam_percentage_and_probe():
    stages = build_cook_stages(
        temperature_c=120, steam=30, probe_temperature_c=63, wait_for_start=True
    )
    cook = stages[-1]
    assert cook["steamGenerators"] == {
        "mode": "steam-percentage",
        "steamPercentage": {"setpoint": 30},
    }
    assert cook["probeAdded"] is True
    assert cook["temperatureProbe"]["setpoint"]["celsius"] == 63
    assert cook["userActionRequired"] is True
    assert stages[0]["userActionRequired"] is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"temperature_c": 260},
        {"temperature_c": 105, "mode": "sous_vide"},
        {"temperature_c": 200, "heating_elements": ["bottom"]},
        {"temperature_c": 150, "heating_elements": []},
        {"temperature_c": 150, "heating_elements": ["side"]},
        {"temperature_c": 150, "probe_temperature_c": 60, "duration_s": 60},
        {"temperature_c": 150, "probe_temperature_c": 120},
        {"temperature_c": 150, "preheat": False, "wait_for_start": True},
    ],
)
def test_invalid_settings_rejected(kwargs):
    with pytest.raises(CookValidationError):
        build_cook_stages(**kwargs)


def test_v1_and_v2_wrappers():
    stages = build_cook_stages(temperature_c=150)
    v1 = build_start_payload(stages, "dev", "oven_v1")
    assert set(v1) == {"stages", "cookId", "originSource"}
    v2 = build_start_payload(stages, "dev", "oven_v2")
    assert v2["type"] == "v2" and v2["cookerId"] == "dev"


def test_custom_stages_keep_ids_and_fill_missing():
    stages = prepare_custom_stages([{"id": "keep", "type": "cook"}, {"type": "cook"}])
    assert stages[0]["id"] == "keep"
    assert stages[1]["id"]
    with pytest.raises(CookValidationError):
        prepare_custom_stages([])
