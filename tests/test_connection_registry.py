import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from uuid import uuid4

from app.services.websocket import ConnectionRegistry


@pytest.fixture
def connection_registry():
    return ConnectionRegistry()


@pytest.fixture
def mock_websocket():
    ws = MagicMock()
    ws.send_json = AsyncMock()
    return ws


class TestConnectionRegistryConnect:
    def test_connect_single(self, connection_registry, mock_websocket):
        user_id = uuid4()
        connection_registry.connect(user_id, mock_websocket)

        assert user_id in connection_registry.active_connections
        assert mock_websocket in connection_registry.active_connections[user_id]
        assert connection_registry.get_connection_count(user_id) == 1

    def test_connect_multiple_same_user(self, connection_registry):
        user_id = uuid4()
        ws1 = MagicMock()
        ws2 = MagicMock()

        connection_registry.connect(user_id, ws1)
        connection_registry.connect(user_id, ws2)

        assert connection_registry.get_connection_count(user_id) == 2
        assert ws1 in connection_registry.active_connections[user_id]
        assert ws2 in connection_registry.active_connections[user_id]

    def test_connect_different_users(self, connection_registry):
        user_id1 = uuid4()
        user_id2 = uuid4()
        ws1 = MagicMock()
        ws2 = MagicMock()

        connection_registry.connect(user_id1, ws1)
        connection_registry.connect(user_id2, ws2)

        assert connection_registry.get_connection_count(user_id1) == 1
        assert connection_registry.get_connection_count(user_id2) == 1
        assert connection_registry.get_total_connections() == 2


class TestConnectionRegistryDisconnect:
    def test_disconnect_single(self, connection_registry, mock_websocket):
        user_id = uuid4()
        connection_registry.connect(user_id, mock_websocket)
        connection_registry.disconnect(user_id, mock_websocket)

        assert user_id not in connection_registry.active_connections

    def test_disconnect_one_of_many(self, connection_registry):
        user_id = uuid4()
        ws1 = MagicMock()
        ws2 = MagicMock()

        connection_registry.connect(user_id, ws1)
        connection_registry.connect(user_id, ws2)
        connection_registry.disconnect(user_id, ws1)

        assert connection_registry.get_connection_count(user_id) == 1
        assert ws2 in connection_registry.active_connections[user_id]
        assert ws1 not in connection_registry.active_connections[user_id]

    def test_disconnect_not_found(self, connection_registry):
        user_id = uuid4()
        ws = MagicMock()

        connection_registry.disconnect(user_id, ws)
        assert user_id not in connection_registry.active_connections


class TestConnectionRegistryBroadcast:
    @pytest.mark.asyncio
    async def test_broadcast_single_connection(self, connection_registry, mock_websocket):
        user_id = uuid4()
        connection_registry.connect(user_id, mock_websocket)

        await connection_registry.broadcast(user_id, {"type": "test", "payload": {}})

        mock_websocket.send_json.assert_called_once_with({"type": "test", "payload": {}})

    @pytest.mark.asyncio
    async def test_broadcast_multiple_connections(self, connection_registry):
        user_id = uuid4()
        ws1 = AsyncMock()
        ws2 = AsyncMock()

        connection_registry.connect(user_id, ws1)
        connection_registry.connect(user_id, ws2)

        await connection_registry.broadcast(user_id, {"event": "test"})

        ws1.send_json.assert_called_once_with({"event": "test"})
        ws2.send_json.assert_called_once_with({"event": "test"})

    @pytest.mark.asyncio
    async def test_broadcast_dead_socket_removed(self, connection_registry):
        user_id = uuid4()
        ws1 = AsyncMock()
        ws2 = AsyncMock()

        ws1.send_json.side_effect = Exception("Connection lost")

        connection_registry.connect(user_id, ws1)
        connection_registry.connect(user_id, ws2)

        await connection_registry.broadcast(user_id, {"event": "test"})

        assert connection_registry.get_connection_count(user_id) == 1
        assert ws2 in connection_registry.active_connections[user_id]
        assert ws1 not in connection_registry.active_connections[user_id]
        ws2.send_json.assert_called_once_with({"event": "test"})

    @pytest.mark.asyncio
    async def test_broadcast_multiple_dead_sockets(self, connection_registry):
        user_id = uuid4()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        ws3 = AsyncMock()

        ws1.send_json.side_effect = Exception("Error 1")
        ws2.send_json.side_effect = Exception("Error 2")

        connection_registry.connect(user_id, ws1)
        connection_registry.connect(user_id, ws2)
        connection_registry.connect(user_id, ws3)

        await connection_registry.broadcast(user_id, {"event": "test"})

        assert connection_registry.get_connection_count(user_id) == 1
        assert ws3 in connection_registry.active_connections[user_id]
        ws3.send_json.assert_called_once_with({"event": "test"})

    @pytest.mark.asyncio
    async def test_broadcast_no_connections(self, connection_registry):
        user_id = uuid4()
        
        await connection_registry.broadcast(user_id, {"event": "test"})
        assert user_id not in connection_registry.active_connections

    @pytest.mark.asyncio
    async def test_broadcast_isolated_users(self, connection_registry):
        user_id1 = uuid4()
        user_id2 = uuid4()
        ws1 = AsyncMock()
        ws2 = AsyncMock()

        connection_registry.connect(user_id1, ws1)
        connection_registry.connect(user_id2, ws2)

        await connection_registry.broadcast(user_id1, {"event": "user1_only"})

        ws1.send_json.assert_called_once_with({"event": "user1_only"})
        ws2.send_json.assert_not_called()


class TestConnectionRegistryMetrics:
    def test_get_connection_count_zero(self, connection_registry):
        user_id = uuid4()
        assert connection_registry.get_connection_count(user_id) == 0

    def test_get_connection_count_multiple(self, connection_registry):
        user_id = uuid4()
        ws1 = MagicMock()
        ws2 = MagicMock()
        ws3 = MagicMock()

        connection_registry.connect(user_id, ws1)
        connection_registry.connect(user_id, ws2)
        connection_registry.connect(user_id, ws3)

        assert connection_registry.get_connection_count(user_id) == 3

    def test_get_total_connections_multiple_users(self, connection_registry):
        user_id1 = uuid4()
        user_id2 = uuid4()
        user_id3 = uuid4()

        connection_registry.connect(user_id1, MagicMock())
        connection_registry.connect(user_id1, MagicMock())
        connection_registry.connect(user_id2, MagicMock())
        connection_registry.connect(user_id3, MagicMock())
        connection_registry.connect(user_id3, MagicMock())
        connection_registry.connect(user_id3, MagicMock())

        assert connection_registry.get_total_connections() == 6


class TestConnectionRegistryEdgeCases:
    def test_duplicate_connect_same_socket(self, connection_registry, mock_websocket):
        user_id = uuid4()
        
        connection_registry.connect(user_id, mock_websocket)
        connection_registry.connect(user_id, mock_websocket)

        assert connection_registry.get_connection_count(user_id) == 2

    @pytest.mark.asyncio
    async def test_broadcast_then_disconnect_race(self, connection_registry):
        user_id = uuid4()
        ws = AsyncMock()
        ws.send_json.side_effect = Exception("Disconnected")

        connection_registry.connect(user_id, ws)
        await connection_registry.broadcast(user_id, {"event": "test"})

        assert connection_registry.get_connection_count(user_id) == 0

    @pytest.mark.asyncio
    async def test_concurrent_broadcast_safety(self, connection_registry):
        import asyncio
        
        user_id = uuid4()
        ws1 = AsyncMock()
        ws2 = AsyncMock()

        connection_registry.connect(user_id, ws1)
        connection_registry.connect(user_id, ws2)

        await asyncio.gather(
            connection_registry.broadcast(user_id, {"msg": "1"}),
            connection_registry.broadcast(user_id, {"msg": "2"}),
        )

        assert ws1.send_json.call_count == 2
        assert ws2.send_json.call_count == 2
