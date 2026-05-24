import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
	AsyncSession,
	async_sessionmaker,
	create_async_engine,
)

from app.core.config import get_settings

_pool_kwargs = {}
try:
	pool_size = int(os.environ.get("TEST_DB_POOL_SIZE", ""))
	max_overflow = int(os.environ.get("TEST_DB_MAX_OVERFLOW", ""))
	if pool_size > 0:
		_pool_kwargs["pool_size"] = pool_size
	if max_overflow >= 0:
		_pool_kwargs["max_overflow"] = max_overflow
except ValueError:
	_pool_kwargs = {}

engine = create_async_engine(
	str(get_settings().DATABASE_URL),
	echo=False,
	**_pool_kwargs,
)

AsyncSessionLocal = async_sessionmaker(
	engine,
	class_=AsyncSession,
	expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
	async with AsyncSessionLocal() as session:
		yield session
