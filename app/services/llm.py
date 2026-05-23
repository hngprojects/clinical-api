"""
LLM provider abstraction.

Wraps both OpenAI and Google Gemini behind a single interface so OCR and AI
interpretation services don't need to know which backend is active.

Provider selection (AI_PROVIDER setting):
  "auto"   — use whichever key is configured; if both are present, OpenAI is
             tried first. On a 429 (quota) or 401 (auth) response, the call is
             transparently retried against the fallback provider.
  "openai" — always OpenAI; raises LLMProviderError immediately if key is absent.
  "gemini" — always Gemini; raises LLMProviderError immediately if key is absent.

Two call types are exposed:
  text_complete(system, user, max_tokens, temperature)  → raw str
  vision_complete(system, user, b64_data, media_type, max_tokens) → raw str

Both return the model's raw text output (callers parse it as JSON themselves).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from contextvars import ContextVar
from typing import Any

import httpx
import redis.asyncio as aioredis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_last_provider_used: ContextVar[str | None] = ContextVar("last_provider_used", default=None)


def get_last_provider() -> str | None:
	"""Return the provider that handled the most recent LLM call in this task."""
	return _last_provider_used.get()


# HTTP status codes that trigger a provider fallback in "auto" mode
_FALLBACK_STATUS_CODES = {401, 403, 429, 502, 503, 504}

# HTTP status codes that count as a circuit-breaker failure.
_CIRCUIT_TRIGGER_CODES = {429, 500, 502, 503, 504}

# Circuit breaker thresholds
_CIRCUIT_FAILURE_THRESHOLD = 2
_CIRCUIT_OPEN_DURATION_SECONDS = 120  # how long the circuit stays open (2 min)
_CIRCUIT_FAILURE_WINDOW_SECONDS = 300  # resets after 5 min inactivity

# Lazy async Redis client — one per event loop / worker process
_async_redis: aioredis.Redis | None = None


async def _get_async_redis() -> aioredis.Redis:
	global _async_redis
	if _async_redis is None:
		settings = get_settings()
		_async_redis = aioredis.Redis.from_url(
			settings.CELERY_BROKER_URL,
			decode_responses=True,
		)
	return _async_redis


# Errors


class LLMProviderError(Exception):
	"""Raised when no provider can fulfil the request."""


class CircuitBreakerOpen(LLMProviderError):
	"""Raised when a provider's circuit breaker is open and no fallback is available."""


# Circuit breaker helpers


async def _is_circuit_open(provider: str) -> bool:
	"""Return True if the circuit for this provider is open (calls should be skipped)."""
	r = await _get_async_redis()
	return bool(await r.exists(f"llm:circuit:{provider}:open"))


async def _record_failure(provider: str) -> None:
	"""
	Increment failure counter for a provider
	"""
	r = await _get_async_redis()
	counter_key = f"llm:circuit:{provider}:failures"
	open_key = f"llm:circuit:{provider}:open"

	count = await r.incr(counter_key)
	await r.expire(counter_key, _CIRCUIT_FAILURE_WINDOW_SECONDS)

	if count >= _CIRCUIT_FAILURE_THRESHOLD:
		await r.set(open_key, "1", ex=_CIRCUIT_OPEN_DURATION_SECONDS)
		logger.warning(
			"[circuit] %s circuit OPENED after %d consecutive failures",
			provider,
			count,
		)


async def _record_success(provider: str) -> None:
	"""Reset the circuit breaker state for a provider on a successful call."""
	r = await _get_async_redis()
	await r.delete(
		f"llm:circuit:{provider}:failures",
		f"llm:circuit:{provider}:open",
	)
	logger.debug("[circuit] %s circuit reset after successful call", provider)


# OpenAI calls


async def _openai_text(
	system: str,
	user: str,
	max_tokens: int,
	temperature: float,
) -> str:
	settings = get_settings()
	payload: dict[str, Any] = {
		"model": settings.OPENAI_MODEL,
		"messages": [
			{"role": "system", "content": system},
			{"role": "user", "content": user},
		],
		"max_tokens": max_tokens,
		"temperature": temperature,
	}
	async with httpx.AsyncClient(timeout=settings.PIPELINE_TIMEOUT_SECONDS) as client:
		response = await client.post(
			"https://api.openai.com/v1/chat/completions",
			headers={
				"Authorization": f"Bearer {settings.OPENAI_API_KEY}",
				"Content-Type": "application/json",
			},
			json=payload,
		)
		if not response.is_success:
			logger.error("[llm] openai text error %s: %s", response.status_code, response.text)
		response.raise_for_status()
	return response.json()["choices"][0]["message"]["content"].strip()


async def _openai_vision(
	system: str,
	user: str,
	b64_data: str,
	media_type: str,
	max_tokens: int,
) -> str:
	settings = get_settings()
	payload: dict[str, Any] = {
		"model": settings.OPENAI_MODEL,
		"messages": [
			{"role": "system", "content": system},
			{
				"role": "user",
				"content": [
					{
						"type": "image_url",
						"image_url": {
							"url": f"data:{media_type};base64,{b64_data}",
							"detail": "high",
						},
					},
					{"type": "text", "text": user},
				],
			},
		],
		"max_tokens": max_tokens,
		"temperature": 0,
	}
	async with httpx.AsyncClient(timeout=settings.PIPELINE_TIMEOUT_SECONDS) as client:
		response = await client.post(
			"https://api.openai.com/v1/chat/completions",
			headers={
				"Authorization": f"Bearer {settings.OPENAI_API_KEY}",
				"Content-Type": "application/json",
			},
			json=payload,
		)
		if not response.is_success:
			logger.error("[llm] openai vision error %s: %s", response.status_code, response.text)
		response.raise_for_status()
	return response.json()["choices"][0]["message"]["content"].strip()


# Gemini calls


def _gemini_url(model: str) -> str:
	return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


async def _gemini_text(
	system: str,
	user: str,
	max_tokens: int,
	temperature: float,
) -> str:
	settings = get_settings()
	payload: dict[str, Any] = {
		"system_instruction": {"parts": [{"text": system}]},
		"contents": [{"parts": [{"text": user}]}],
		"generationConfig": {
			"temperature": temperature,
			"maxOutputTokens": max_tokens,
			"responseMimeType": "application/json",
		},
	}
	async with httpx.AsyncClient(timeout=settings.PIPELINE_TIMEOUT_SECONDS) as client:
		response = await client.post(
			_gemini_url(settings.GEMINI_MODEL),
			headers={"Content-Type": "application/json", "x-goog-api-key": settings.GEMINI_API_KEY},
			json=payload,
		)
		response.raise_for_status()
	return response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


async def _gemini_vision(
	system: str,
	user: str,
	b64_data: str,
	media_type: str,
	max_tokens: int,
) -> str:
	settings = get_settings()
	payload: dict[str, Any] = {
		"system_instruction": {"parts": [{"text": system}]},
		"contents": [
			{
				"parts": [
					{"inline_data": {"mime_type": media_type, "data": b64_data}},
					{"text": user},
				]
			}
		],
		"generationConfig": {
			"temperature": 0,
			"maxOutputTokens": max_tokens,
			"responseMimeType": "application/json",
		},
	}
	async with httpx.AsyncClient(timeout=settings.PIPELINE_TIMEOUT_SECONDS) as client:
		response = await client.post(
			_gemini_url(settings.GEMINI_MODEL),
			headers={"Content-Type": "application/json", "x-goog-api-key": settings.GEMINI_API_KEY},
			json=payload,
		)
		response.raise_for_status()
	return response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


# Provider resolution


def _resolve_order() -> list[str]:
	"""
	Return the list of providers to try, in order.

	- "openai" / "gemini": single-item list (strict mode).
	- "auto": both providers ordered by key availability.
		If both keys are set, OpenAI goes first.
	"""
	settings = get_settings()
	provider = settings.AI_PROVIDER.lower()

	if provider == "openai":
		if not settings.OPENAI_API_KEY:
			raise LLMProviderError("AI_PROVIDER=openai but OPENAI_API_KEY is not set.")
		return ["openai"]

	if provider == "gemini":
		if not settings.GEMINI_API_KEY:
			raise LLMProviderError("AI_PROVIDER=gemini but GEMINI_API_KEY is not set.")
		return ["gemini"]

	# auto mode
	order: list[str] = []
	if settings.OPENAI_API_KEY:
		order.append("openai")
	if settings.GEMINI_API_KEY:
		order.append("gemini")

	if not order:
		raise LLMProviderError(
			"No LLM API key is configured. Set OPENAI_API_KEY or GEMINI_API_KEY in your environment."
		)
	return order


# Public interface


async def text_complete(
	system: str,
	user: str,
	*,
	max_tokens: int = 1200,
	temperature: float = 0.2,
) -> str:
	"""
	Send a text-only prompt to the active LLM and return the raw response string.

	Falls back to the secondary provider in auto mode on quota / auth errors.
	Skips providers whose circuit breaker is open.
	"""
	order = _resolve_order()
	last_exc: Exception | None = None

	for provider in order:
		# Skip this provider if its circuit is open
		if await _is_circuit_open(provider):
			logger.warning("[circuit] %s circuit is open — skipping for text_complete", provider)
			last_exc = CircuitBreakerOpen(f"{provider} circuit breaker is open")
			continue

		try:
			if provider == "openai":
				result = await _openai_text(system, user, max_tokens, temperature)
			else:
				result = await _gemini_text(system, user, max_tokens, temperature)

			await _record_success(provider)
			_last_provider_used.set(provider)
			if len(order) > 1:
				logger.debug("[llm] text_complete fulfilled by %s", provider)
			return result

		except httpx.HTTPStatusError as exc:
			if exc.response.status_code in _CIRCUIT_TRIGGER_CODES:
				await _record_failure(provider)
			if exc.response.status_code in _FALLBACK_STATUS_CODES and len(order) > 1:
				logger.warning(
					"[llm] %s returned %s — falling back to next provider",
					provider,
					exc.response.status_code,
				)
				last_exc = exc
				continue
			raise

		except (httpx.TimeoutException, httpx.ConnectError) as exc:
			await _record_failure(provider)
			logger.warning("[llm] %s network error (%s) — falling back", provider, exc)
			last_exc = exc
			if len(order) > 1:
				continue
			raise

		except Exception as exc:
			last_exc = exc
			if len(order) > 1:
				logger.warning("[llm] %s error (%s) — falling back", provider, exc)
				continue
			raise

	raise LLMProviderError(f"All configured LLM providers failed. Last error: {last_exc}") from last_exc


async def vision_complete(
	system: str,
	user: str,
	b64_data: str,
	media_type: str,
	*,
	max_tokens: int = 1500,
) -> str:
	"""
	Send a base64-encoded file + prompt to the active LLM and return the raw response string.

	Falls back to the secondary provider in auto mode on quota / auth errors.
	Skips providers whose circuit breaker is open.
	"""
	order = _resolve_order()
	last_exc: Exception | None = None

	for provider in order:
		# OpenAI vision only accepts image types — PDFs cause a 400.
		# Skip and send to Gemini to handle it/
		if provider == "openai" and media_type == "application/pdf":
			logger.debug("[llm] skipping openai for PDF — not supported, trying next provider")
			last_exc = LLMProviderError("OpenAI vision does not support PDF files")
			continue

		if await _is_circuit_open(provider):
			logger.warning("[circuit] %s circuit is open — skipping for vision_complete", provider)
			last_exc = CircuitBreakerOpen(f"{provider} circuit breaker is open")
			continue

		try:
			if provider == "openai":
				result = await _openai_vision(system, user, b64_data, media_type, max_tokens)
			else:
				result = await _gemini_vision(system, user, b64_data, media_type, max_tokens)

			await _record_success(provider)
			_last_provider_used.set(provider)
			if len(order) > 1:
				logger.debug("[llm] vision_complete fulfilled by %s", provider)
			return result

		except httpx.HTTPStatusError as exc:
			if exc.response.status_code in _CIRCUIT_TRIGGER_CODES:
				await _record_failure(provider)
			if exc.response.status_code in _FALLBACK_STATUS_CODES and len(order) > 1:
				logger.warning(
					"[llm] %s returned %s — falling back to next provider",
					provider,
					exc.response.status_code,
				)
				last_exc = exc
				continue
			raise

		except (httpx.TimeoutException, httpx.ConnectError) as exc:
			# Network-level failures count toward the circuit
			await _record_failure(provider)
			logger.warning("[llm] %s network error (%s) — falling back", provider, exc)
			last_exc = exc
			if len(order) > 1:
				continue
			raise

		except Exception as exc:
			last_exc = exc
			if len(order) > 1:
				logger.warning("[llm] %s error (%s) — falling back", provider, exc)
				continue
			raise

	raise LLMProviderError(f"All configured LLM providers failed. Last error: {last_exc}") from last_exc


async def _openai_stream(
	system: str,
	user: str,
	max_tokens: int,
	temperature: float,
) -> AsyncGenerator[str, None]:
	"""
	Calls OpenAI with stream=True and yields tokens as they arrive.
	OpenAI streams responses as Server-Sent Events (SSE) —
	each line looks like: data: {"choices": [{"delta": {"content": "Hi"}}]}
	We parse each line and yield just the text content.
	"""
	settings = get_settings()
	payload: dict[str, Any] = {
		"model": settings.OPENAI_MODEL,
		"messages": [
			{"role": "system", "content": system},
			{"role": "user", "content": user},
		],
		"max_tokens": max_tokens,
		"temperature": temperature,
		"stream": True,
	}
	async with httpx.AsyncClient(timeout=settings.PIPELINE_TIMEOUT_SECONDS) as client:
		async with client.stream(  # ← client.stream() instead of client.post()
			"POST",
			"https://api.openai.com/v1/chat/completions",
			headers={
				"Authorization": f"Bearer {settings.OPENAI_API_KEY}",
				"Content-Type": "application/json",
			},
			json=payload,
		) as response:
			response.raise_for_status()
			async for line in response.aiter_lines():  # read one SSE line at a time
				if not line.startswith("data: "):
					continue
				chunk = line[6:]
				if chunk.strip() == "[DONE]":
					return
				try:
					data = json.loads(chunk)
					token = data["choices"][0]["delta"].get("content")
					if token:
						yield token
				except (KeyError, json.JSONDecodeError):
					continue


async def stream_text_complete(
	system: str,
	user: str,
	*,
	max_tokens: int = 1200,
	temperature: float = 0.2,
) -> AsyncGenerator[str, None]:
	"""
	Public streaming interface — mirrors text_complete() but yields tokens.

	OpenAI: true token-by-token streaming.
	Gemini: falls back to one big yield of the full response.
	Gemini streaming uses a completely different binary protocol — not worth
	the complexity for this feature.
	"""
	order = _resolve_order()

	if order[0] == "openai":
		async for token in _openai_stream(system, user, max_tokens, temperature):
			yield token
	else:
		# Gemini fallback — still works, just not word-by-word
		result = await _gemini_text(system, user, max_tokens, temperature)
		yield result
