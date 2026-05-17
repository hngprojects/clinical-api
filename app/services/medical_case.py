from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.ai_interpretation import AIInterpretation
from app.models.chat import Chat
from app.models.lab_result import LabResult
from app.models.medical_case import MedicalCase, MedicalCaseStatus
from app.models.user import User
from app.repositories.ai_interpretation import AIInterpretationRepository
from app.repositories.chat import ChatRepository
from app.repositories.lab_result import LabResultRepository
from app.repositories.medical_case import MedicalCaseRepository
from app.schemas.medical_case import MedicalCaseCreate, MedicalCaseUpdate


@dataclass(frozen=True)
class CaseFullDetail:
	"""Case row plus related entities for history/detail views."""

	case: MedicalCase
	lab_results: list[LabResult]
	interpretation: AIInterpretation | None
	chats: list[Chat]


async def create_case(
	case_repo: MedicalCaseRepository,
	payload: MedicalCaseCreate,
) -> MedicalCase:
	"""Create a new medical case."""
	case = MedicalCase(
		user_id=payload.user_id,
		guest_session_id=payload.guest_session_id,
		status=payload.status,
	)
	case_repo.add(case)
	await case_repo.commit()
	await case_repo.refresh(case)
	return case


async def create_case_for_user(
	case_repo: MedicalCaseRepository,
	user: User,
) -> MedicalCase:
	"""Shorthand: create a PENDING case for an authenticated user."""
	case = MedicalCase(
		user_id=user.id,
		status=MedicalCaseStatus.PENDING,
	)
	case_repo.add(case)
	await case_repo.commit()
	await case_repo.refresh(case)
	return case


async def get_case(
	case_repo: MedicalCaseRepository,
	case_id: UUID,
	*,
	user: User | None = None,
	guest_session_id: str | None = None,
) -> MedicalCase:
	"""Fetch a single case, enforcing ownership by user or guest_session_id."""
	case = await case_repo.get_by_id(case_id)
	if case is None:
		raise NotFoundError("Medical case not found.")
	if user is not None and case.user_id != user.id:
		raise ForbiddenError("You do not have access to this case.")
	if user is None and guest_session_id is not None and case.guest_session_id != guest_session_id:
		raise ForbiddenError("You do not have access to this case.")
	return case


async def get_case_full(
	case_repo: MedicalCaseRepository,
	lab_repo: LabResultRepository,
	interp_repo: AIInterpretationRepository,
	chat_repo: ChatRepository,
	case_id: UUID,
	*,
	user: User | None = None,
	guest_session_id: str | None = None,
) -> CaseFullDetail:
	"""Load case with all lab results, latest interpretation, and full chat history."""
	if user is None and guest_session_id is None:
		raise ForbiddenError("You must be authenticated to access this case.")
	if user is not None and guest_session_id is not None:
		raise ForbiddenError("You cannot access a case with both user and guest session.")
	case = await get_case(case_repo, case_id, user=user, guest_session_id=guest_session_id)
	lab_count = await lab_repo.count_by_case(case_id)
	lab_results = await lab_repo.list_by_case(case_id, offset=0, limit=lab_count) if lab_count else []
	interpretation = await interp_repo.get_latest_for_case(case_id)
	chat_count = await chat_repo.count_by_case(case_id)
	chats = await chat_repo.list_by_case(case_id, offset=0, limit=chat_count) if chat_count else []
	return CaseFullDetail(
		case=case,
		lab_results=lab_results,
		interpretation=interpretation,
		chats=chats,
	)


async def list_cases_for_user(
	case_repo: MedicalCaseRepository,
	user_id: UUID,
	*,
	offset: int = 0,
	limit: int = 50,
) -> tuple[list[MedicalCase], int]:
	"""Return paginated cases for a user together with the total count."""
	cases = await case_repo.list_by_user(user_id, offset=offset, limit=limit)
	total = await case_repo.count_by_user(user_id)
	return cases, total


async def update_case(
	case_repo: MedicalCaseRepository,
	case_id: UUID,
	payload: MedicalCaseUpdate,
	*,
	user: User | None = None,
) -> MedicalCase:
	"""Partially update a medical case."""
	case = await get_case(case_repo, case_id, user=user)
	if payload.status is not None:
		case.status = payload.status
	if payload.completed_at is not None:
		case.completed_at = payload.completed_at
	await case_repo.commit()
	await case_repo.refresh(case)
	return case


async def complete_case(
	case_repo: MedicalCaseRepository,
	case_id: UUID,
	*,
	user: User | None = None,
) -> MedicalCase:
	"""Mark a case as complete."""
	case = await get_case(case_repo, case_id, user=user)
	case.status = MedicalCaseStatus.COMPLETE
	case.completed_at = datetime.now(timezone.utc)
	await case_repo.commit()
	await case_repo.refresh(case)
	return case


async def delete_owned_case(
	case_repo: MedicalCaseRepository,
	case_id: UUID,
	*,
	user: User,
) -> None:
	"""Delete a medical case owned by the given user. Related rows cascade from the DB."""
	case = await case_repo.get_by_id(case_id)
	if case is None:
		raise NotFoundError("Medical case not found.")
	if case.user_id != user.id:
		raise ForbiddenError("You do not have access to this case.")
	await case_repo.delete(case)
	await case_repo.commit()
