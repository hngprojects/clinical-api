import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_root(client: AsyncClient) -> None:
	response = await client.get("/")
	assert response.status_code == 200
	assert "running" in response.json()["message"]


async def test_health(client: AsyncClient) -> None:
	response = await client.get("/api/v1/health")
	assert response.status_code == 200
	assert response.json() == {"status": "ok"}
