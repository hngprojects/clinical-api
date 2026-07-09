import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, Response, RequestError, Request
from app.core.config import get_settings

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_subscribe_success(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
	"""Test successful subscription proxy to MailerLite on v1 path."""
	settings = get_settings()
	monkeypatch.setattr(settings, "MAILERLITE_API_KEY", "test-api-key")

	mock_response = Response(201, json={"id": "123"})

	with patch("app.api.v1.endpoints.subscribe.httpx.AsyncClient") as mock_client_cls:
		mock_client = AsyncMock()
		mock_client.post.return_value = mock_response
		mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
		mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

		response = await client.post(
			"/api/v1/subscribe",
			json={
				"first_name": "Ngozi",
				"email": "ngozi@example.com",
				"group_id": "191612485430675393",
				"tags": ["lead_magnet_guide"]
			}
		)

		assert response.status_code == 200
		body = response.json()
		assert body["status"] == "success"
		assert body["message"] == "Subscribed successfully."

		mock_client.post.assert_called_once()
		args, kwargs = mock_client.post.call_args
		assert args[0] == "https://connect.mailerlite.com/api/subscribers"
		assert kwargs["json"]["email"] == "ngozi@example.com"
		assert kwargs["json"]["fields"]["name"] == "Ngozi"
		assert kwargs["json"]["groups"] == ["191612485430675393"]
		assert kwargs["json"]["tags"] == ["lead_magnet_guide"]
		assert kwargs["headers"]["Authorization"] == "Bearer test-api-key"


async def test_subscribe_validation_error(client: AsyncClient) -> None:
	"""Test validation error for invalid email structure."""
	response = await client.post(
		"/api/v1/subscribe",
		json={
			"first_name": "Ngozi",
			"email": "not-an-email",
			"group_id": "191612485430675393"
		}
	)
	assert response.status_code == 422


async def test_subscribe_missing_api_key(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
	"""Test 500 status when MAILERLITE_API_KEY is not set."""
	settings = get_settings()
	monkeypatch.setattr(settings, "MAILERLITE_API_KEY", None)

	response = await client.post(
		"/api/v1/subscribe",
		json={
			"first_name": "Ngozi",
			"email": "ngozi@example.com",
			"group_id": "191612485430675393"
		}
	)
	assert response.status_code == 500
	assert "service is temporarily unavailable" in response.json()["message"]


async def test_subscribe_mailerlite_error(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
	"""Test 502 status when MailerLite API returns non-200 response."""
	settings = get_settings()
	monkeypatch.setattr(settings, "MAILERLITE_API_KEY", "test-api-key")

	mock_response = Response(400, text="Bad Request")

	with patch("app.api.v1.endpoints.subscribe.httpx.AsyncClient") as mock_client_cls:
		mock_client = AsyncMock()
		mock_client.post.return_value = mock_response
		mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
		mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

		response = await client.post(
			"/api/v1/subscribe",
			json={
				"first_name": "Ngozi",
				"email": "ngozi@example.com",
				"group_id": "191612485430675393"
			}
		)

		assert response.status_code == 502
		assert "Subscription failed at MailerLite" in response.json()["message"]


async def test_subscribe_request_error(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
	"""Test 503 status when MailerLite request fails (e.g., timeout/network error)."""
	settings = get_settings()
	monkeypatch.setattr(settings, "MAILERLITE_API_KEY", "test-api-key")

	with patch("app.api.v1.endpoints.subscribe.httpx.AsyncClient") as mock_client_cls:
		mock_client = AsyncMock()
		mock_client.post.side_effect = RequestError("Timeout", request=Request("POST", "https://example.com"))
		mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
		mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

		response = await client.post(
			"/api/v1/subscribe",
			json={
				"first_name": "Ngozi",
				"email": "ngozi@example.com",
				"group_id": "191612485430675393"
			}
		)

		assert response.status_code == 503
		assert "Subscription provider is unreachable" in response.json()["message"]
