import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/test")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-min-32-characters-long-padding")
os.environ.setdefault("OTP_PEPPER", "test-otp-pepper-min-32-characters-long-padding")

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.base import Base
from app.models.user import User, UserRole
from app.services.auth.tokens import create_access_token


class _FakeRedis:
	"""In-memory Redis stand-in for rate-limit tests."""

	def __init__(self) -> None:
		self._counts: dict[str, int] = {}

	async def incr(self, key: str) -> int:
		self._counts[key] = self._counts.get(key, 0) + 1
		return self._counts[key]

	async def expire(self, key: str, ttl: int) -> bool:  # noqa: ARG002
		return True


@pytest.fixture(autouse=True)
def mock_redis_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Avoid requiring a live Redis broker during API tests."""
	fake = _FakeRedis()

	async def _get_redis() -> _FakeRedis:
		return fake

	monkeypatch.setattr("app.core.rate_limit.get_redis", _get_redis)


@pytest.fixture(scope="session", autouse=True)
async def setup_database(request: pytest.FixtureRequest):
    """
    Create all tables once at the start of the test session, drop them at the end.
    Runs automatically for every test — no opt-in required.
    """
    session = request.session
    needs_db = any(item.get_closest_marker("no_db") is None for item in session.items)
    if not needs_db:
        yield
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)



@pytest.fixture
async def client():
    """Unauthenticated AsyncClient wired to the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac



@pytest.fixture
async def test_user():
    """
    Insert a real User row into the test DB and yield it.

    Uses a unique email per invocation so parallel tests never collide.
    On teardown, deletes the user — the CASCADE on medical_cases, lab_results,
    and ai_interpretation means all related rows are cleaned up automatically.
    """
    user = User(
        id=uuid.uuid4(),
        email=f"test_{uuid.uuid4().hex[:8]}@clinsights.dev",
        first_name="Test",
        last_name="User",
        role=UserRole.PATIENT,
        is_active=True,
        is_email_verified=True,
    )
    async with AsyncSessionLocal() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    yield user
    async with AsyncSessionLocal() as session:
        existing = await session.get(User, user.id)
        if existing:
            await session.delete(existing)
            await session.commit()


@pytest.fixture
def auth_headers(test_user: User) -> dict[str, str]:
    """Return a Bearer Authorization header signed for the test user."""
    token, _ = create_access_token(test_user.id)
    return {"Authorization": f"Bearer {token}"}
