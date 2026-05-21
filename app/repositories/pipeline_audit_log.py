import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_audit_log import PipelineAuditLog
from app.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class PipelineAuditLogRepository(BaseRepository[PipelineAuditLog]):
	model = PipelineAuditLog

	async def add_event(
		self,
		*,
		lab_result_id: UUID,
		event: str,
		status_before: str | None = None,
		status_after: str | None = None,
		provider: str | None = None,
		duration_ms: int | None = None,
		attempt: int | None = None,
		error: str | None = None,
	) -> PipelineAuditLog:
		entry = PipelineAuditLog(
			lab_result_id=lab_result_id,
			event=event,
			status_before=status_before,
			status_after=status_after,
			provider=provider,
			duration_ms=duration_ms,
			attempt=attempt,
			error=error,
		)
		self._session.add(entry)
		await self._session.flush()
		return entry

	async def list_by_lab_result(self, lab_result_id: UUID) -> list[PipelineAuditLog]:
		result = await self._session.execute(
			select(PipelineAuditLog)
			.where(PipelineAuditLog.lab_result_id == lab_result_id)
			.order_by(PipelineAuditLog.created_at.asc())
		)
		return list(result.scalars().all())


async def _log_event(
	session: AsyncSession,
	*,
	lab_result_id: UUID,
	event: str,
	status_before: str | None = None,
	status_after: str | None = None,
	provider: str | None = None,
	duration_ms: int | None = None,
	attempt: int | None = None,
	error: str | None = None,
) -> None:
	"""Write a pipeline audit log entry. Never raises — a failed write must not crash the pipeline."""
	try:
		repo = PipelineAuditLogRepository(session)
		await repo.add_event(
			lab_result_id=lab_result_id,
			event=event,
			status_before=status_before,
			status_after=status_after,
			provider=provider,
			duration_ms=duration_ms,
			attempt=attempt,
			error=error,
		)
	except Exception as exc:
		logger.warning(
			"[audit_log] Failed to write audit event '%s' for lab_result_id=%s: %s",
			event,
			lab_result_id,
			exc,
		)
