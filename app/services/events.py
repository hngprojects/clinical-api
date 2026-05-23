import asyncio
import inspect
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
			close = getattr(self.redis, "close", None)
			aclose = getattr(self.redis, "aclose", None)
			if callable(close):
				try:
					result = close()
					if inspect.isawaitable(result):
						await result
				except Exception:
					logger.exception("EventBus failed to close sync connection")
			if callable(aclose):
				try:
					result = aclose()
					if inspect.isawaitable(result):
						await result
				except Exception:
					logger.exception("EventBus failed to aclose async connection")
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

		idle_seconds = 0
		KEEPALIVE_INTERVAL = 30

		try:
			while True:
				# Non-blocking check — get_message() with no timeout returns None
				# immediately if there is nothing in the buffer. This avoids
				# relying on redis-py's internal asyncio.wait_for which can
				# terminate the generator silently on timeout in Python 3.12+.
				message = await pubsub.get_message(ignore_subscribe_messages=True)

				if message is not None:
					idle_seconds = 0
					try:
						data = json.loads(message["data"])
						yield data
					except (json.JSONDecodeError, KeyError, TypeError):
						logger.error("Failed to deserialize message on channel %s", channel)
				else:
					# No message — sleep 1s then check again
					await asyncio.sleep(1)
					idle_seconds += 1
					if idle_seconds >= KEEPALIVE_INTERVAL:
						idle_seconds = 0
						yield None  # keepalive signal for the SSE endpoint

		except asyncio.CancelledError:
			logger.debug(f"Subscription cancelled for channel: {channel}")
			raise  # must re-raise so cancellation propagates to the caller
		except Exception:
			logger.exception(f"Subscription error for {channel}")
		finally:
			await pubsub.unsubscribe(channel)
			await pubsub.aclose()
			logger.debug(f"Unsubscribed from channel: {channel}")
