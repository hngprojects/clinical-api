import os

import pytest

# Dummy values so settings can be imported without a .env
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/test")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-min-32-characters-long-padding")
os.environ.setdefault("OTP_PEPPER", "test-otp-pepper-min-32-characters-long-padding")
os.environ.setdefault("STORAGE_PROVIDER", "local")


@pytest.fixture(scope="session", autouse=True)
async def setup_database():
    """No-op override — unit tests don't need a real database."""
    yield
