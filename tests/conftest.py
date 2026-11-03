"""Fixtures for Anova Precision Oven tests."""
from __future__ import annotations

import copy
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anova_precision_oven.const import DOMAIN

DEVICE_ID = "01237909c1bb01cb01"

# Idle state captured from a real oven_v1 (firmware 2.1.16) via diagnostics.
IDLE_STATE: dict[str, Any] = {
    "version": 1,
    "systemInfo": {
        "online": True,
        "hardwareVersion": "120V1",
        "firmwareVersion": "2.1.16",
        "triacsFailed": False,
    },
    "state": {"mode": "idle", "temperatureUnit": "F"},
    "nodes": {
        "temperatureBulbs": {
            "mode": "dry",
            "wet": {"current": {"celsius": 21.87, "fahrenheit": 71.36}, "dosed": False, "doseFailed": False},
            "dry": {"current": {"celsius": 22.29, "fahrenheit": 72.12}, "setpoint": {"celsius": 50, "fahrenheit": 122}},
            "dryTop": {"current": {"celsius": 22.29, "fahrenheit": 72.12}, "overheated": False},
            "dryBottom": {"current": {"celsius": 22.25, "fahrenheit": 72.05}, "overheated": False},
        },
        "timer": {"mode": "idle", "initial": 0, "current": 0},
        "temperatureProbe": {"connected": False},
        "steamGenerators": {
            "mode": "idle",
            "relativeHumidity": {"current": 97},
            "evaporator": {"failed": False, "overheated": False, "celsius": 22.33, "watts": 0},
            "boiler": {"descaleRequired": True, "failed": False, "overheated": False, "celsius": 22.33, "watts": 0, "dosed": False},
        },
        "heatingElements": {
            "top": {"on": False, "failed": False, "watts": 0},
            "bottom": {"on": False, "failed": False, "watts": 0},
            "rear": {"on": True, "failed": False, "watts": 0},
        },
        "fan": {"speed": 0, "failed": False},
        "vent": {"open": True},
        "waterTank": {"empty": False},
        "door": {"closed": True},
        "lamp": {"on": False, "failed": False, "preference": "on"},
        "userInterfaceCircuit": {"communicationFailed": False},
    },
}

DEVICE_INFO = {
    "cookerId": DEVICE_ID,
    "name": "Anova Precision Oven",
    "type": "oven_v1",
}


def idle_state() -> dict[str, Any]:
    return copy.deepcopy(IDLE_STATE)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    yield


class FakeWebSocket:
    """Records sent messages; optionally answers each with a RESPONSE."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.coordinator = None
        self.reply: dict[str, Any] | None = {"status": "ok"}

    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        self.sent.append(message)
        if self.reply is not None and self.coordinator is not None:
            await self.coordinator._handle_message(
                json.dumps(
                    {
                        "command": "RESPONSE",
                        "requestId": message["requestId"],
                        "payload": self.reply,
                    }
                )
            )

    async def close(self) -> None:
        return None


@pytest.fixture
async def setup_oven(hass):
    """Set up the integration with a fake websocket and one idle v1 oven."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={"token": "anova-test"}, unique_id="anova-test"
    )
    entry.add_to_hass(hass)
    ws = FakeWebSocket()

    with patch(
        "custom_components.anova_precision_oven.coordinator.AnovaOvenCoordinator.start_websocket",
        new=AsyncMock(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    ws.coordinator = coordinator
    coordinator.websocket = ws
    coordinator._running = True  # as if the websocket task were live

    async def push(state: dict[str, Any]) -> None:
        await coordinator._handle_message(
            json.dumps({"command": "EVENT_APO_STATE", "payload": {"cookerId": DEVICE_ID, "state": state}})
        )
        await hass.async_block_till_done()

    await coordinator._handle_message(
        json.dumps({"command": "EVENT_APO_WIFI_LIST", "payload": [DEVICE_INFO]})
    )
    await push(idle_state())
    yield entry, coordinator, ws, push
    with patch.object(coordinator, "stop_websocket", new=AsyncMock()):
        await hass.config_entries.async_unload(entry.entry_id)
