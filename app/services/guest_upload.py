"""Guest upload quota (after pipeline success) and failure cleanup."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.guest_upload_lock import (
	clear_guest_upload_counted,
	mark_guest_upload_counted,
	release_guest_upload_lock,
)
from app.db.session import AsyncSessionLocal
from app.models.medical_case import MedicalCase
from app.repositories.guest_session import GuestSessionRepository
from app.services.guest_sessions import GuestSessionManager
from app.services.storage import delete_medical_file_by_url

logger = logging.getLogger(__name__)


async def _get_guest_case(session: AsyncSession, case_id: UUID) -> MedicalCase | None:
	result = await session.execute(
		select(MedicalCase).where(MedicalCase.id == case_id).options(selectinload(MedicalCase.lab_results))
	)
	case = result.scalar_one_or_none()
	if case is None or case.user_id is not None or case.guest_session_id is None:
		return None
	return case


async def purge_guest_failed_upload(case_id: UUID) -> None:
	"""Remove guest case artifacts and storage when OCR/interpretation fails.

	Uses its own DB session so it does not conflict with the pipeline session
	(which already has lab_result rows loaded in the identity map).
	"""
	guest_session_id: UUID | None = None
	file_urls: list[str] = []

	try:
		async with AsyncSessionLocal() as session:
			case = await _get_guest_case(session, case_id)
			if case is None:
				return

			guest_session_id = case.guest_session_id
			for lab_result in case.lab_results:
				file_url = (lab_result.file or {}).get("url")
				if file_url:
					file_urls.append(file_url)

			# Core DELETE so Postgres ON DELETE CASCADE runs (ORM delete would null FKs).
			await session.execute(delete(MedicalCase).where(MedicalCase.id == case_id))
			await session.commit()

		for file_url in file_urls:
			delete_medical_file_by_url(file_url)

		if guest_session_id is not None:
			logger.info(
				"Purged failed guest upload case_id=%s guest_session_id=%s",
				case_id,
				guest_session_id,
			)
	finally:
		if guest_session_id is not None:
			await clear_guest_upload_counted(case_id)
			await release_guest_upload_lock(guest_session_id)


async def complete_guest_upload_quota(case_id: UUID) -> None:
	"""Increment guest upload quota after successful OCR and interpretation."""
	if not await mark_guest_upload_counted(case_id):
		return

	guest_session_id: UUID | None = None
	async with AsyncSessionLocal() as session:
		case = await _get_guest_case(session, case_id)
		if case is None:
			await clear_guest_upload_counted(case_id)
			return

		guest_session_id = case.guest_session_id
		assert guest_session_id is not None

		manager = GuestSessionManager(GuestSessionRepository(session))
		try:
			await manager.increment_upload(guest_session_id)
		except Exception:
			await clear_guest_upload_counted(case_id)
			await release_guest_upload_lock(guest_session_id)
			raise

	if guest_session_id is not None:
		await release_guest_upload_lock(guest_session_id)
		logger.info("Guest upload quota applied case_id=%s guest_session_id=%s", case_id, guest_session_id)
