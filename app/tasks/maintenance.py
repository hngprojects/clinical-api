"""Scheduled maintenance tasks (guest session cleanup, etc.)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from celery import shared_task

logger = logging.getLogger(__name__)

MAINTENANCE_QUEUE = "default"


@shared_task(
	name="app.tasks.maintenance.purge_expired_guest_sessions",
	queue=MAINTENANCE_QUEUE,
)
def purge_expired_guest_sessions() -> int:
	"""Delete guest session rows that expired or were revoked before the retention window."""
	return asyncio.run(_purge_expired_guest_sessions_async())


async def _purge_expired_guest_sessions_async() -> int:
	from app.core.config import get_settings
	from app.db.session import AsyncSessionLocal
	from app.repositories.guest_session import GuestSessionRepository

	settings = get_settings()
	cutoff = datetime.now(timezone.utc) - timedelta(days=settings.GUEST_SESSION_RETENTION_DAYS)

	async with AsyncSessionLocal() as session:
		repo = GuestSessionRepository(session)
		deleted = await repo.delete_stale(cutoff=cutoff)
		await session.commit()

	logger.info("[maintenance] purged %d stale guest_sessions (cutoff=%s)", deleted, cutoff.isoformat())
	return deleted
