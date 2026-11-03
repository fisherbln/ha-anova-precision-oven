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
    assert cook["fan"] == {"speed": 100}  # rear element locks the fan
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


def test_retarget_keeps_settings_and_follows_preheat():
    from custom_components.anova_precision_oven.cook import retarget_stages

    stages = build_cook_stages(
        temperature_c=150, heating_elements=["top", "bottom"], fan_speed=60, duration_s=600
    )
    preheat, cook = stages
    out = retarget_stages(stages, preheat["id"], 200)
    assert [s["id"] for s in out] == [preheat["id"], cook["id"]]
    for stage in out:
        assert stage["temperatureBulbs"]["dry"]["setpoint"]["celsius"] == 200
        assert stage["heatingElements"] == cook["heatingElements"]
        assert stage["fan"] == {"speed": 60}
        assert "steamGenerators" not in stage
    assert out[1]["timer"] == {"initial": 600}
    # the input isn't modified
    assert stages[0]["temperatureBulbs"]["dry"]["setpoint"]["celsius"] == 150


def test_retarget_mid_cook_carries_timer_and_leaves_other_temps():
    from custom_components.anova_precision_oven.cook import retarget_stages

    first = build_cook_stages(temperature_c=150, preheat=False, duration_s=600)[0]
    second = build_cook_stages(temperature_c=220, preheat=False)[0]
    out = retarget_stages([first, second], first["id"], 160, timer_remaining_s=245)
    assert out[0]["temperatureBulbs"]["dry"]["setpoint"]["celsius"] == 160
    assert out[0]["timer"] == {"initial": 245}
    assert out[1]["temperatureBulbs"]["dry"]["setpoint"]["celsius"] == 220


def test_retarget_limits_and_unknown_stage():
    from custom_components.anova_precision_oven.cook import retarget_stages

    sv = build_cook_stages(temperature_c=57, mode="sous_vide", preheat=False)
    with pytest.raises(CookValidationError):
        retarget_stages(sv, sv[0]["id"], 120)
    assert retarget_stages(sv, "missing", 60) is None



@pytest.mark.parametrize(
    ("kwargs", "fan"),
    [
        ({"heating_elements": ["top", "bottom"], "fan_speed": 40}, 40),
        ({"heating_elements": ["top", "rear"], "fan_speed": 40}, 100),
        ({"heating_elements": ["top"], "fan_speed": 40, "steam": 10}, 100),
        ({"heating_elements": ["bottom"], "fan_speed": 40, "steam": 0}, 40),
        ({"heating_elements": ["top"], "fan_speed": 40, "mode": "sous_vide"}, 100),
    ],
)
def test_fan_locked_high_with_rear_or_steam(kwargs, fan):
    temp = 60 if kwargs.get("mode") == "sous_vide" else 150
    (cook,) = build_cook_stages(temperature_c=temp, preheat=False, **kwargs)
    assert cook["fan"] == {"speed": fan}
