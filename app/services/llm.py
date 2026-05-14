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

import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# HTTP status codes that trigger a provider fallback in "auto" mode
_FALLBACK_STATUS_CODES = {401, 403, 429}


# Errors


class LLMProviderError(Exception):
	"""Raised when no provider can fulfil the request."""


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
	"""
	order = _resolve_order()
	last_exc: Exception | None = None

	for provider in order:
		try:
			if provider == "openai":
				result = await _openai_text(system, user, max_tokens, temperature)
			else:
				result = await _gemini_text(system, user, max_tokens, temperature)
			if len(order) > 1:
				logger.debug("[llm] text_complete fulfilled by %s", provider)
			return result
		except httpx.HTTPStatusError as exc:
			if exc.response.status_code in _FALLBACK_STATUS_CODES and len(order) > 1:
				logger.warning(
					"[llm] %s returned %s — falling back to next provider",
					provider,
					exc.response.status_code,
				)
				last_exc = exc
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
	"""
	order = _resolve_order()
	last_exc: Exception | None = None

	for provider in order:
		try:
			if provider == "openai":
				result = await _openai_vision(system, user, b64_data, media_type, max_tokens)
			else:
				result = await _gemini_vision(system, user, b64_data, media_type, max_tokens)
			if len(order) > 1:
				logger.debug("[llm] vision_complete fulfilled by %s", provider)
			return result
		except httpx.HTTPStatusError as exc:
			if exc.response.status_code in _FALLBACK_STATUS_CODES and len(order) > 1:
				logger.warning(
					"[llm] %s returned %s — falling back to next provider",
					provider,
					exc.response.status_code,
				)
				last_exc = exc
				continue
			raise
		except Exception as exc:
			last_exc = exc
			if len(order) > 1:
				logger.warning("[llm] %s error (%s) — falling back", provider, exc)
				continue
			raise

	raise LLMProviderError(f"All configured LLM providers failed. Last error: {last_exc}") from last_exc
