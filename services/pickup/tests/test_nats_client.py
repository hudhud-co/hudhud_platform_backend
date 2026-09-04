"""Live NATS client connect regression tests — mocked nats-py only."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from pickup.config import RuntimeEnvironment, load_settings
from pickup.infrastructure.nats.client import LiveNatsJetStreamClient


def test_async_connect_assigns_nats_client_instance() -> None:
    """nats-py connect() returns None; store the client instance explicitly."""
    settings = load_settings(
        environment=RuntimeEnvironment.LOCAL,
        relay_enabled=True,
        nats_url="nats://localhost:4222",
        nats_dev_no_auth=True,
    )
    client = LiveNatsJetStreamClient(settings)

    mock_instance = MagicMock()
    mock_instance.connect = AsyncMock(return_value=None)
    mock_instance.jetstream.return_value = MagicMock()

    with patch(
        "pickup.infrastructure.nats.client.NatsClient",
        return_value=mock_instance,
    ):
        client._run(client._async_connect())

    assert client._client is mock_instance
    mock_instance.connect.assert_awaited_once()
    mock_instance.jetstream.assert_called_once()
