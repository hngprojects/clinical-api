from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medical_upload import MedicalUpload


class MedicalUploadRepository:
	"""Encapsulates all database operations for the MedicalUpload model."""

	def __init__(self, session: AsyncSession):
		self._session = session

	def add(self):
		self._session.add()

	async def commit(self):
		await self._session.commit()

	async def flush(self):
		await self._session.flush()

	async def refresh(self, medical_upload: MedicalUpload):
		await self._session.refresh(medical_upload)

	async def rollback(self):
		await self._session.rollback()
