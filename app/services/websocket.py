import asyncio
import json
import logging
from uuid import UUID

from fastapi import WebSocket

logger = logging.getLogger(__name__)

MAX_WEBSOCKET_MESSAGE_BYTES = 256_000


class ConnectionRegistry:
	def __init__(self):
		self.active_connections: dict[UUID, list[WebSocket]] = {}
		self._lock = asyncio.Lock()

	async def connect(self, user_id: UUID, websocket: WebSocket) -> None:
		async with self._lock:
			if user_id not in self.active_connections:
				self.active_connections[user_id] = []
			self.active_connections[user_id].append(websocket)
			logger.debug(
				"WebSocket connected",
				extra={
					"user_id": str(user_id),
					"connection_count": len(self.active_connections[user_id]),
				},
			)

	async def disconnect(self, user_id: UUID, websocket: WebSocket) -> None:
		async with self._lock:
			if user_id in self.active_connections:
				try:
					self.active_connections[user_id].remove(websocket)
					if not self.active_connections[user_id]:
						del self.active_connections[user_id]
					logger.debug(
						"WebSocket disconnected",
						extra={"user_id": str(user_id)},
					)
				except ValueError:
					logger.warning(
						"WebSocket not found in registry",
						extra={"user_id": str(user_id)},
					)

	async def broadcast(self, user_id: UUID, message: dict) -> None:
		if user_id not in self.active_connections:
			return

		message_json = json.dumps(message)
		if len(message_json.encode("utf-8")) > MAX_WEBSOCKET_MESSAGE_BYTES:
			logger.warning("WebSocket message too large", extra={"user_id": str(user_id)})
			return

		async with self._lock:
			connections = self.active_connections[user_id][:]

		dead_sockets = []
		for websocket in connections:
			try:
				await websocket.send_json(message)
			except Exception:
				logger.warning(
					"Failed to send message to WebSocket",
					extra={"user_id": str(user_id)},
				)
				dead_sockets.append(websocket)

		for websocket in dead_sockets:
			self.disconnect(user_id, websocket)

	def get_connection_count(self, user_id: UUID) -> int:
		return len(self.active_connections.get(user_id, []))

	def get_total_connections(self) -> int:
		return sum(len(conns) for conns in self.active_connections.values())
