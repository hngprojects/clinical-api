import pytest
import json
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4

from app.services.events import EventBus


class TestEventBusConnect:

    @pytest.mark.asyncio
    async def test_connect_failure(self):
        with patch("redis.asyncio.from_url") as mock_from_url:
            mock_from_url.side_effect = Exception("Connection failed")

            bus = EventBus("redis://localhost:6379")

            with pytest.raises(Exception):
                await bus.connect()

    @pytest.mark.asyncio
    async def test_connect_redis_not_responding(self):
        with patch("redis.asyncio.from_url") as mock_from_url:
            mock_redis = AsyncMock()
            mock_redis.ping.side_effect = Exception("Redis not responding")
            mock_from_url.return_value = mock_redis

            bus = EventBus("redis://localhost:6379")

            with pytest.raises(Exception):
                await bus.connect()


class TestEventBusDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect(self):
        mock_redis = AsyncMock()
        bus = EventBus("redis://localhost:6379")
        bus.redis = mock_redis

        await bus.disconnect()

        mock_redis.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_idempotent(self):
        bus = EventBus("redis://localhost:6379")
        bus.redis = None
        
        await bus.disconnect()


class TestEventBusPublish:
    @pytest.mark.asyncio
    async def test_publish_success(self):
        mock_redis = AsyncMock()
        bus = EventBus("redis://localhost:6379")
        bus.redis = mock_redis

        user_id = uuid4()
        event_type = "test_event"
        payload = {"key": "value"}

        await bus.publish(user_id, event_type, payload)

        mock_redis.publish.assert_called_once()
        call_args = mock_redis.publish.call_args
        channel = call_args[0][0]
        message = call_args[0][1]

        assert channel == f"events:{user_id}"
        data = json.loads(message)
        assert data["type"] == event_type
        assert data["payload"] == payload

    @pytest.mark.asyncio
    async def test_publish_empty_payload(self):
        mock_redis = AsyncMock()
        bus = EventBus("redis://localhost:6379")
        bus.redis = mock_redis

        user_id = uuid4()

        await bus.publish(user_id, "test", {})

        call_args = mock_redis.publish.call_args
        message = call_args[0][1]
        data = json.loads(message)

        assert data["payload"] == {}

    @pytest.mark.asyncio
    async def test_publish_large_payload(self):
        mock_redis = AsyncMock()
        bus = EventBus("redis://localhost:6379")
        bus.redis = mock_redis

        user_id = uuid4()
        large_payload = {"data": "x" * 10000}

        await bus.publish(user_id, "test", large_payload)

        call_args = mock_redis.publish.call_args
        message = call_args[0][1]
        data = json.loads(message)

        assert data["payload"] == large_payload

    @pytest.mark.asyncio
    async def test_publish_redis_error(self):
        mock_redis = AsyncMock()
        mock_redis.publish.side_effect = Exception("Redis error")

        bus = EventBus("redis://localhost:6379")
        bus.redis = mock_redis

        user_id = uuid4()

        await bus.publish(user_id, "test", {})

    @pytest.mark.asyncio
    async def test_publish_not_connected(self):
        bus = EventBus("redis://localhost:6379")
        bus.redis = None

        await bus.publish(uuid4(), "test", {})


class TestEventBusEdgeCases:
    @pytest.mark.asyncio
    async def test_publish_concurrent_multiple_events(self):
        mock_redis = AsyncMock()
        bus = EventBus("redis://localhost:6379")
        bus.redis = mock_redis

        user_id = uuid4()

        await asyncio.gather(
            bus.publish(user_id, "event1", {"msg": "1"}),
            bus.publish(user_id, "event2", {"msg": "2"}),
            bus.publish(user_id, "event3", {"msg": "3"}),
        )

        assert mock_redis.publish.call_count == 3

    @pytest.mark.asyncio
    async def test_publish_special_characters(self):
        mock_redis = AsyncMock()
        bus = EventBus("redis://localhost:6379")
        bus.redis = mock_redis

        user_id = uuid4()
        payload = {
            "text": "Hello 世界 🌍",
            "symbols": "!@#$%^&*()",
        }

        await bus.publish(user_id, "test", payload)

        call_args = mock_redis.publish.call_args
        message = call_args[0][1]
        data = json.loads(message)

        assert data["payload"]["text"] == "Hello 世界 🌍"
        assert data["payload"]["symbols"] == "!@#$%^&*()"
