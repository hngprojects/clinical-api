import os
import uuid

# Must be set BEFORE any app module is imported.
# app/db/session.py calls get_settings() at module level when creating the engine,
# so these need to be in os.environ before `from app.main import app` runs.
# setdefault means they won't override values already set in the shell or .env.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://clinsightlocal_user:clinsightLocal2026@localhost:5432/clinsight")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-min-32-characters-long-padding")
os.environ.setdefault("OTP_PEPPER", "test-otp-pepper-min-32-characters-long-padding")

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.base import Base
from app.models.user import User, UserRole
from app.services.auth.tokens import create_access_token


# ── Schema lifecycle ──────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
async def setup_database():
    """
    Create all tables once at the start of the test session, drop them at the end.
    Runs automatically for every test — no opt-in required.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ── HTTP client ───────────────────────────────────────────────────────────�

@pytest.fixture
async def client():
    """Unauthenticated AsyncClient wired to the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Test user + auth helpers ──────────────────────────────────────────────────

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
