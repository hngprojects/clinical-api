"""Test doubles for Redis used across guest session tests."""

from __future__ import annotations


class FakeRedis:
	"""Minimal async Redis stand-in with optional per-key TTL tracking."""

	def __init__(self) -> None:
		self._data: dict[str, str] = {}
		self._ttl: dict[str, int] = {}

	async def set(self, key: str, value: str, ex: int | None = None) -> bool:
		self._data[key] = value
		if ex is not None:
			self._ttl[key] = ex
		return True

	async def exists(self, key: str) -> int:
		return 1 if key in self._data else 0

	async def ttl(self, key: str) -> int:
		if key not in self._data:
			return -2
		return self._ttl.get(key, -1)

	async def expire(self, key: str, ttl: int) -> bool:
		if key not in self._data:
			return False
		self._ttl[key] = ttl
		return True

	async def delete(self, key: str) -> int:
		if key in self._data:
			del self._data[key]
			self._ttl.pop(key, None)
			return 1
		return 0
