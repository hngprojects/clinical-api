import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/test")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-min-32-characters-long-padding")
os.environ.setdefault("OTP_PEPPER", "test-otp-pepper-min-32-characters-long-padding")

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.config import get_settings
import app.db.session as _db_session
from app.main import app
from app.models.base import Base
from app.models.user import User, UserRole
from app.services.auth.tokens import create_access_token


# ---------------------------------------------------------------------------
# Modules that bind AsyncSessionLocal (or engine) by name at import time.
# We must patch each one's local reference as well as the source module.
# ---------------------------------------------------------------------------
_ASYNC_SESSION_LOCAL_TARGETS = [
    "app.api.v1.endpoints.ws_chat",
    "app.services.guest",
    # tasks are imported lazily inside functions, but patch defensively anyway
    "app.tasks.maintenance",
    "app.tasks.pipeline",
]


class _FakeRedis:
	"""In-memory Redis stand-in for rate-limit and guest-upload lock tests."""

	def __init__(self) -> None:
		self._counts: dict[str, int] = {}
		self._strings: dict[str, str] = {}

	async def incr(self, key: str) -> int:
		self._counts[key] = self._counts.get(key, 0) + 1
		return self._counts[key]

	async def expire(self, key: str, ttl: int) -> bool:  # noqa: ARG002
		return True

	async def get(self, key: str) -> str | None:
		if key in self._strings:
			return self._strings[key]
		if key not in self._counts:
			return None
		return str(self._counts[key])

	async def set(
		self,
		key: str,
		value: str,
		*,
		nx: bool = False,
		ex: int | None = None,  # noqa: ARG002
	) -> bool | None:
		if nx and key in self._strings:
			return None
		self._strings[key] = value
		return True

	async def delete(self, key: str) -> int:
		removed = 0
		if self._counts.pop(key, None) is not None:
			removed = 1
		if self._strings.pop(key, None) is not None:
			removed = 1
		return removed


@pytest.fixture(autouse=True)
def mock_redis_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Avoid requiring a live Redis broker during API tests."""
	fake = _FakeRedis()

	async def _get_redis() -> _FakeRedis:
		return fake

	monkeypatch.setattr("app.core.rate_limit.get_redis", _get_redis)
	monkeypatch.setattr("app.core.redis_client.get_redis", _get_redis)
	monkeypatch.setattr("app.core.guest_upload_lock.get_redis", _get_redis)


@pytest.fixture(scope="session", autouse=True)
async def setup_database(request: pytest.FixtureRequest):
    """
    Swap the app's engine for a NullPool engine (no connection caching across
    event loops), create all tables once, then drop them at session end.

    NullPool is the standard fix for asyncpg "Future attached to a different
    loop" errors: without pooling, every acquire() opens a fresh connection on
    the current loop instead of re-using one bound to a previous loop.
    """
    import importlib
    import sys

    session = request.session
    needs_db = any(item.get_closest_marker("no_db") is None for item in session.items)
    if not needs_db:
        yield
        return

    nullpool_engine = create_async_engine(
        str(get_settings().DATABASE_URL),
        echo=False,
        poolclass=NullPool,
    )
    nullpool_factory = async_sessionmaker(
        nullpool_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Patch the source module.
    _db_session.engine = nullpool_engine
    _db_session.AsyncSessionLocal = nullpool_factory

    # Patch every module that already bound AsyncSessionLocal by name.
    for mod_name in _ASYNC_SESSION_LOCAL_TARGETS:
        if mod_name in sys.modules:
            sys.modules[mod_name].AsyncSessionLocal = nullpool_factory

    # Also patch celery_app's engine reference.
    if "app.core.celery_app" in sys.modules:
        sys.modules["app.core.celery_app"].engine = nullpool_engine

    async with nullpool_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield



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
    async with _db_session.AsyncSessionLocal() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    yield user
    async with _db_session.AsyncSessionLocal() as session:
        existing = await session.get(User, user.id)
        if existing:
            await session.delete(existing)
            await session.commit()


@pytest.fixture
def auth_headers(test_user: User) -> dict[str, str]:
    """Return a Bearer Authorization header signed for the test user."""
    token, _ = create_access_token(test_user.id)
    return {"Authorization": f"Bearer {token}"}