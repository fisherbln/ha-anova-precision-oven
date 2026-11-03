"""The reconnect loop handles a rejected token without crashing."""
from unittest.mock import patch

import websockets
from websockets.datastructures import Headers
from websockets.http11 import Response

from custom_components.anova_precision_oven.coordinator import AnovaOvenCoordinator


async def test_rejected_token_logs_auth_failure(hass, caplog):
    coordinator = AnovaOvenCoordinator(hass, "anova-bad")
    coordinator._running = True
    rejected = websockets.exceptions.InvalidStatus(Response(401, "Unauthorized", Headers()))

    async def fake_sleep(_delay):
        coordinator._running = False

    with patch("websockets.connect", side_effect=rejected), patch(
        "custom_components.anova_precision_oven.coordinator.asyncio.sleep", fake_sleep
    ):
        await coordinator._listen_websocket()
    assert "Authentication failed (401)" in caplog.text
    assert "anova-bad" not in caplog.text
    await coordinator.stop_websocket()


def test_redact_filter_handles_a_lone_mapping_argument():
    import logging

    from custom_components.anova_precision_oven.coordinator import _TokenRedactFilter

    record = logging.LogRecord("x", logging.DEBUG, __file__, 1, "data %(a)s", ({"a": "anova-secret"},), None)
    assert _TokenRedactFilter("anova-secret").filter(record)
    assert record.getMessage() == "data [REDACTED]"
