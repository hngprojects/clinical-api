from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.doctor_verification import DoctorVerification, DoctorVerificationAuditLog, DoctorVerificationDocument


class DoctorVerificationRepository:
	"""Encapsulates all database operations for DoctorVerification and related tables."""

	def __init__(self, session: AsyncSession) -> None:
		self._session = session

	async def get_by_id(self, verification_id: UUID, lock: bool = False) -> DoctorVerification | None:
		"""Fetch a verification record by ID, optionally locking the row."""
		stmt = (
			select(DoctorVerification)
			.where(DoctorVerification.id == verification_id)
			.options(
				selectinload(DoctorVerification.documents),
				selectinload(DoctorVerification.audit_logs),
			)
		)
		if lock:
			stmt = stmt.with_for_update()
		result = await self._session.execute(stmt)
		return result.scalar_one_or_none()

	async def get_by_user_id(self, user_id: UUID, lock: bool = False) -> DoctorVerification | None:
		"""Fetch a verification record by User/Doctor ID, optionally locking the row."""
		stmt = (
			select(DoctorVerification)
			.where(DoctorVerification.user_id == user_id)
			.options(
				selectinload(DoctorVerification.documents),
				selectinload(DoctorVerification.audit_logs),
			)
		)
		if lock:
			stmt = stmt.with_for_update()
		result = await self._session.execute(stmt)
		return result.scalar_one_or_none()

	async def get_document_by_id(self, document_id: UUID) -> DoctorVerificationDocument | None:
		"""Fetch a document record by ID."""
		stmt = (
			select(DoctorVerificationDocument)
			.where(DoctorVerificationDocument.id == document_id)
			.options(selectinload(DoctorVerificationDocument.verification))
		)
		result = await self._session.execute(stmt)
		return result.scalar_one_or_none()

	def add(self, verification: DoctorVerification) -> None:
		self._session.add(verification)

	def add_document(self, document: DoctorVerificationDocument) -> None:
		self._session.add(document)

	def add_audit_log(self, audit_log: DoctorVerificationAuditLog) -> None:
		self._session.add(audit_log)

	async def delete_document(self, document: DoctorVerificationDocument) -> None:
		await self._session.delete(document)

	async def flush(self) -> None:
		await self._session.flush()

	async def commit(self) -> None:
		await self._session.commit()

	async def refresh(self, instance: object) -> None:
		await self._session.refresh(instance)

	async def rollback(self) -> None:
		await self._session.rollback()
