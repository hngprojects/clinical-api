import asyncio
import json
import logging
from typing import AsyncIterator
from uuid import UUID

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

MAX_EVENT_TYPE_LENGTH = 128
MAX_EVENT_PAYLOAD_BYTES = 256_000


class EventBus:
	def __init__(self, redis_url: str):
		self.redis_url = redis_url
		self.redis: aioredis.Redis | None = None

	async def connect(self) -> None:
		try:
			self.redis = await aioredis.from_url(
				self.redis_url,
				encoding="utf-8",
				decode_responses=True,
			)
			await self.redis.ping()
			logger.info("EventBus connected to Redis")
		except Exception as e:
			logger.error(f"EventBus Redis connection failed: {e}")
			raise

	async def disconnect(self) -> None:
		if self.redis:
			await self.redis.aclose()
			logger.info("EventBus disconnected from Redis")

	async def publish(
		self,
		user_id: UUID,
		event_type: str,
		payload: dict,
	) -> None:
		if not self.redis:
			logger.error("EventBus not connected")
			return

		if not event_type or len(event_type) > MAX_EVENT_TYPE_LENGTH:
			logger.error("Invalid event type")
			return

		channel = f"events:{user_id}"
		message = {
			"type": event_type,
			"payload": payload,
		}
		message_json = json.dumps(message)
		if len(message_json.encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
			logger.error("Event payload too large")
			return

		try:
			await self.redis.publish(channel, message_json)
			logger.debug(f"Event published: {channel} type={event_type}")
		except Exception:
			logger.error("Failed to publish event")

	async def subscribe(
		self,
		user_id: UUID,
	) -> AsyncIterator[dict | None]:
		if not self.redis:
			logger.error("EventBus not connected")
			return

		channel = f"events:{user_id}"
		pubsub = self.redis.pubsub()
		await pubsub.subscribe(channel)
		logger.debug(f"Subscribed to channel: {channel}")

		idle_timeout = 30

		try:
			while True:
				try:
					message = await asyncio.wait_for(
						pubsub.get_message(ignore_subscribe_messages=True),
						timeout=idle_timeout,
					)
				except asyncio.TimeoutError:
					yield None
					continue

				if message:
					try:
						data = json.loads(message["data"])
						yield data
					except (json.JSONDecodeError, KeyError, TypeError):
						logger.error("Failed to deserialize message")
						continue
				else:
					yield None

		except asyncio.CancelledError:
			logger.debug(f"Subscription cancelled for channel: {channel}")
		except Exception:
			logger.error(f"Subscription error for {channel}")
		finally:
			await pubsub.unsubscribe(channel)
			await pubsub.aclose()
			logger.debug(f"Unsubscribed from channel: {channel}")
