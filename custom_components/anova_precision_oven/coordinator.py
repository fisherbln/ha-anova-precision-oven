"""Data update coordinator for Anova Precision Oven."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import ssl
import uuid
from typing import Any
from urllib.parse import urlencode

import websockets
from websockets.exceptions import WebSocketException

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ACCESSORIES_APO,
    CMD_APO_STOP,
    DOMAIN,
    EVENT_APO_STATE,
    EVENT_APO_WIFI_LIST,
    RESPONSE,
    RESPONSE_TIMEOUT,
    WS_ENDPOINT,
    WS_MAX_MESSAGE_SIZE,
    WS_RECONNECT_DELAY,
)

_LOGGER = logging.getLogger(__name__)

_REDACTED = "[REDACTED]"

_INTEGRATION_LOGGER_NAMES = (
    "custom_components.anova_precision_oven",
    "custom_components.anova_precision_oven.coordinator",
    "custom_components.anova_precision_oven.config_flow",
    "custom_components.anova_precision_oven.sensor",
    "custom_components.anova_precision_oven.binary_sensor",
    "custom_components.anova_precision_oven.climate",
    "custom_components.anova_precision_oven.switch",
    "custom_components.anova_precision_oven.entity",
    "custom_components.anova_precision_oven.diagnostics",
    "custom_components.anova_precision_oven.cook",
    "custom_components.anova_precision_oven.event",
    "custom_components.anova_precision_oven.services",
)


class _TokenRedactFilter(logging.Filter):
    """Redact the Anova token from log records.

    Defense-in-depth against transport-layer exceptions whose repr may
    include the token-bearing WebSocket URL. Installed on each
    integration logger because logging filters do not propagate to
    child loggers.
    """

    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token

    def filter(self, record: logging.LogRecord) -> bool:
        token = self._token
        if not token:
            return True
        try:
            if isinstance(record.msg, str) and token in record.msg:
                record.msg = record.msg.replace(token, _REDACTED)
            if isinstance(record.args, tuple):
                record.args = tuple(
                    arg.replace(token, _REDACTED) if isinstance(arg, str) else arg
                    for arg in record.args
                )
            elif isinstance(record.args, dict):
                # logging passes a lone mapping argument through as a dict
                record.args = {
                    key: value.replace(token, _REDACTED)
                    if isinstance(value, str)
                    else value
                    for key, value in record.args.items()
                }
        except Exception:
            pass
        return True


class AnovaOvenCoordinator(DataUpdateCoordinator):
    """Coordinator to manage Anova Oven WebSocket connection and data updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        token: str,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
        )
        self.token = token
        self.websocket = None
        self.devices: dict[str, dict[str, Any]] = {}
        self._running = False
        self._listen_task = None
        self._ssl_context: ssl.SSLContext | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        # Devices whose running cook should be stopped when its timer ends
        self.stop_when_timer_done: set[str] = set()

        self._log_filter = _TokenRedactFilter(token)
        for name in _INTEGRATION_LOGGER_NAMES:
            logging.getLogger(name).addFilter(self._log_filter)

    def _build_connection_url(self) -> str:
        """Build the WebSocket connection URL."""
        params = {
            "token": self.token,
            "supportedAccessories": ACCESSORIES_APO,
        }
        return f"{WS_ENDPOINT}?{urlencode(params)}"

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from WebSocket connection."""
        if not self._running:
            await self.start_websocket()
        return self.devices

    async def _create_ssl_context(self) -> ssl.SSLContext:
        """Create SSL context in executor to avoid blocking."""
        def _create_context():
            return ssl.create_default_context()

        return await self.hass.async_add_executor_job(_create_context)

    async def start_websocket(self) -> None:
        """Start the WebSocket connection and listener."""
        if self._running:
            return

        try:
            if self._ssl_context is None:
                self._ssl_context = await self._create_ssl_context()

            self._running = True
            self._listen_task = self.hass.async_create_task(self._listen_websocket())
        except Exception:
            _LOGGER.error("Failed to start WebSocket", exc_info=True)

    async def stop_websocket(self) -> None:
        """Stop the WebSocket connection."""
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self.websocket:
            await self.websocket.close()
            self.websocket = None

        for name in _INTEGRATION_LOGGER_NAMES:
            logging.getLogger(name).removeFilter(self._log_filter)

    async def _listen_websocket(self) -> None:
        """Listen to WebSocket messages with automatic reconnection."""
        while self._running:
            try:
                url = self._build_connection_url()
                _LOGGER.debug("Connecting to Anova WebSocket API")

                try:
                    websocket = await asyncio.wait_for(
                        websockets.connect(
                            url,
                            ssl=self._ssl_context,
                            open_timeout=10,
                            close_timeout=5,
                            max_size=WS_MAX_MESSAGE_SIZE,
                        ),
                        timeout=15,
                    )
                except asyncio.TimeoutError:
                    _LOGGER.error("WebSocket connection timed out after 15 seconds")
                    await asyncio.sleep(WS_RECONNECT_DELAY)
                    continue
                except websockets.exceptions.InvalidStatus as err:
                    status_code = err.response.status_code
                    _LOGGER.error(
                        "WebSocket connection rejected with status %s",
                        status_code,
                    )
                    if status_code in (401, 403):
                        _LOGGER.error(
                            "Authentication failed (%s) -- check your token",
                            status_code,
                        )
                    await asyncio.sleep(WS_RECONNECT_DELAY)
                    continue
                except Exception as err:
                    _LOGGER.error(
                        "Failed to establish WebSocket connection: %s",
                        type(err).__name__,
                    )
                    await asyncio.sleep(WS_RECONNECT_DELAY)
                    continue

                async with websocket:
                    self.websocket = websocket
                    _LOGGER.debug("Connected to Anova WebSocket API")

                    async for message in websocket:
                        if not self._running:
                            break
                        await self._handle_message(message)

            except WebSocketException as err:
                _LOGGER.error(
                    "WebSocket connection error: %s", type(err).__name__
                )
                if self._running:
                    await asyncio.sleep(WS_RECONNECT_DELAY)
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.error(
                    "Unexpected error in WebSocket listener: %s", type(err).__name__
                )
                if self._running:
                    await asyncio.sleep(WS_RECONNECT_DELAY)

    async def _handle_message(self, message: str) -> None:
        """Handle incoming WebSocket messages."""
        try:
            if len(message) > WS_MAX_MESSAGE_SIZE:
                _LOGGER.warning(
                    "Received oversized message (%d bytes), ignoring", len(message)
                )
                return

            data = json.loads(message)

            if not isinstance(data, dict):
                _LOGGER.warning("Received invalid message format (not a dict)")
                return

            command = data.get("command")
            if not command:
                _LOGGER.debug("Received message without command field")
                return

            if command == RESPONSE or command.startswith(f"{RESPONSE}_"):
                self._handle_response(data)
                return

            if command == EVENT_APO_WIFI_LIST:
                devices = data.get("payload", [])
                if not isinstance(devices, list):
                    _LOGGER.warning("Invalid payload format for WIFI_LIST event")
                    return

                for device in devices:
                    if not isinstance(device, dict):
                        continue

                    device_id = device.get("cookerId")
                    if not device_id:
                        continue

                    is_new = device_id not in self.devices
                    if is_new:
                        self.devices[device_id] = {"state": {}}
                    self.devices[device_id]["info"] = device

                    if is_new:
                        _LOGGER.info(
                            "Discovered device: %s (%s)",
                            device.get("name", "Unknown"),
                            device_id,
                        )

                if devices:
                    self.async_set_updated_data(self.devices)

            elif command == EVENT_APO_STATE:
                payload = data.get("payload", {})
                if not isinstance(payload, dict):
                    _LOGGER.warning("Invalid payload format for STATE event")
                    return

                device_id = payload.get("cookerId")

                if not device_id:
                    _LOGGER.warning(
                        "Received state update without cookerId, cannot identify device"
                    )
                    return

                if device_id not in self.devices:
                    _LOGGER.debug(
                        "Received state for unknown device %s, initializing", device_id
                    )
                    self.devices[device_id] = {}

                actual_state = payload.get("state", {})
                if not isinstance(actual_state, dict):
                    _LOGGER.warning("Invalid state format for device %s", device_id)
                    return

                previous = self.devices[device_id].get("state") or {}
                self._track_timer(device_id, previous, actual_state)
                self.devices[device_id]["state"] = actual_state
                self.async_set_updated_data(self.devices)

        except json.JSONDecodeError as err:
            _LOGGER.warning("Failed to decode WebSocket message: %s", err)
        except Exception as err:
            _LOGGER.error(
                "Error handling WebSocket message: %s", type(err).__name__
            )
            _LOGGER.debug("Message handling error details", exc_info=True)

    def _track_timer(
        self, device_id: str, previous: dict[str, Any], current: dict[str, Any]
    ) -> None:
        """Count finished timers and stop the oven afterwards if asked to.

        The oven's timer counts down while "running" and then resets to idle
        without reporting "completed", and the cook keeps heating, so a
        finished timer is a running timer that stops running mid-cook.
        """
        device = self.devices[device_id]
        mode = (current.get("state") or {}).get("mode")
        if mode in (None, "idle"):
            self.stop_when_timer_done.discard(device_id)
            return
        was = ((previous.get("nodes") or {}).get("timer") or {}).get("mode")
        now = ((current.get("nodes") or {}).get("timer") or {}).get("mode")
        if was != "running" or now in ("running", "paused"):
            return
        device["timer_finished_count"] = device.get("timer_finished_count", 0) + 1
        if device_id in self.stop_when_timer_done:
            self.stop_when_timer_done.discard(device_id)
            self.hass.async_create_task(self._async_stop_after_timer(device_id))

    async def _async_stop_after_timer(self, device_id: str) -> None:
        _LOGGER.info("Timer finished; stopping the oven as requested")
        try:
            await self.send_command(device_id, CMD_APO_STOP)
        except Exception as err:  # noqa: BLE001 - logged for the user
            _LOGGER.error("Could not stop the oven after its timer: %s", err)

    def _handle_response(self, data: dict[str, Any]) -> None:
        """Resolve the pending command a RESPONSE message answers."""
        _LOGGER.debug("Command response: %s", json.dumps(data))
        request_id = data.get("requestId")
        future = self._pending.get(request_id) if request_id else None
        if future is None and len(self._pending) == 1:
            # Response without a requestId we know: only one command is in
            # flight, so it must be the answer to that one.
            future = next(iter(self._pending.values()))
        if future is not None and not future.done():
            payload = data.get("payload")
            future.set_result(payload if isinstance(payload, dict) else {})

    async def send_command(
        self, device_id: str, command: str, payload: dict[str, Any] | None = None
    ) -> None:
        """Send a command to a device and check the oven's response.

        Raises HomeAssistantError if the oven rejects the command. If no
        response arrives within RESPONSE_TIMEOUT the command is assumed to
        have gone through (not every command is answered).
        """
        if not self.websocket:
            raise UpdateFailed("WebSocket not connected")

        if not device_id or not isinstance(device_id, str):
            raise UpdateFailed("Invalid device_id")

        if not command or not isinstance(command, str):
            raise UpdateFailed("Invalid command")

        if payload is not None and not isinstance(payload, dict):
            raise UpdateFailed("Invalid payload format")

        request_id = str(uuid.uuid4())
        message = {
            "command": command,
            "requestId": request_id,
            "payload": {
                "id": device_id,
                "type": command,
            },
        }

        if payload:
            message["payload"]["payload"] = payload

        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = future
        try:
            await self.websocket.send(json.dumps(message))
            _LOGGER.debug("Sent command %s to device %s", command, device_id)
            try:
                response = await asyncio.wait_for(future, RESPONSE_TIMEOUT)
            except asyncio.TimeoutError:
                _LOGGER.debug("No response to %s within %ss", command, RESPONSE_TIMEOUT)
                return
        except WebSocketException as err:
            _LOGGER.error(
                "Failed to send command %s: %s", command, type(err).__name__
            )
            raise UpdateFailed(
                f"Failed to send command: {type(err).__name__}"
            ) from err
        except Exception as err:
            _LOGGER.error(
                "Unexpected error sending command %s: %s",
                command,
                type(err).__name__,
            )
            raise UpdateFailed(
                f"Unexpected error: {type(err).__name__}"
            ) from err
        finally:
            self._pending.pop(request_id, None)

        status = str(response.get("status", "")).lower()
        if status in ("error", "failed", "failure"):
            error = response.get("error") or response.get("message") or response
            raise HomeAssistantError(f"Oven rejected {command}: {error}")

    def get_device_state(self, device_id: str) -> dict[str, Any] | None:
        """Get device state by ID."""
        device = self.devices.get(device_id)
        if device:
            return device.get("state")
        return None

    def get_device_info(self, device_id: str) -> dict[str, Any] | None:
        """Get device info by ID."""
        device = self.devices.get(device_id)
        if device:
            return device.get("info")
        return None


@dataclass
class AnovaOvenRuntimeData:
    """Runtime data stored on the config entry."""

    coordinator: AnovaOvenCoordinator


AnovaOvenConfigEntry = ConfigEntry[AnovaOvenRuntimeData]
