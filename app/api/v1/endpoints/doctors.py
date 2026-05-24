import logging

from fastapi import APIRouter, File, Request, UploadFile, status

from app.api.deps import DBSession, DoctorUser
from app.core.responses import SuccessResponse
from app.repositories.doctor_profile import DoctorProfileRepository
from app.schemas.doctor_profile import (
	CredentialsRequest,
	DoctorProfileResponse,
	ProfessionalInfoRequest,
)
from app.services.doctor_profile import (
	save_credentials,
	save_medical_license,
	save_passport_photo,
	save_professional_info,
	submit_for_verification,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/doctors", tags=["doctors"])


def _profile_repo(session: DBSession) -> DoctorProfileRepository:
	return DoctorProfileRepository(session)


# ── Step 1 — Professional Information ────────────────────────────────────────


@router.post(
	"/me/profile",
	response_model=SuccessResponse[DoctorProfileResponse],
	status_code=status.HTTP_200_OK,
)
async def save_profile(
	payload: ProfessionalInfoRequest,
	current_user: DoctorUser,
	session: DBSession,
) -> SuccessResponse[DoctorProfileResponse]:
	"""Save or update professional information (Step 1)."""
	repo = _profile_repo(session)
	profile = await save_professional_info(repo, current_user, payload)
	return SuccessResponse(
		message="Professional information saved.",
		data=DoctorProfileResponse.model_validate(profile),
	)


@router.post(
	"/me/profile/passport",
	response_model=SuccessResponse[DoctorProfileResponse],
	status_code=status.HTTP_200_OK,
)
async def upload_passport(
	request: Request,
	current_user: DoctorUser,
	session: DBSession,
	file: UploadFile = File(...),
) -> SuccessResponse[DoctorProfileResponse]:
	"""Upload passport photograph (Step 1)."""
	repo = _profile_repo(session)
	public_url_base = str(request.base_url).rstrip("/")
	profile = await save_passport_photo(repo, current_user, file, public_url_base)
	return SuccessResponse(
		message="Passport photo uploaded.",
		data=DoctorProfileResponse.model_validate(profile),
	)


# ── Step 2 — Credential Verification ─────────────────────────────────────────


@router.post(
	"/me/credentials",
	response_model=SuccessResponse[DoctorProfileResponse],
	status_code=status.HTTP_200_OK,
)
async def save_doctor_credentials(
	payload: CredentialsRequest,
	current_user: DoctorUser,
	session: DBSession,
) -> SuccessResponse[DoctorProfileResponse]:
	"""Save MDCN license number and NIN (Step 2)."""
	repo = _profile_repo(session)
	profile = await save_credentials(repo, current_user, payload)
	return SuccessResponse(
		message="Credentials saved.",
		data=DoctorProfileResponse.model_validate(profile),
	)


@router.post(
	"/me/credentials/license",
	response_model=SuccessResponse[DoctorProfileResponse],
	status_code=status.HTTP_200_OK,
)
async def upload_license(
	request: Request,
	current_user: DoctorUser,
	session: DBSession,
	file: UploadFile = File(...),
) -> SuccessResponse[DoctorProfileResponse]:
	"""Upload medical license document (Step 2)."""
	repo = _profile_repo(session)
	public_url_base = str(request.base_url).rstrip("/")
	profile = await save_medical_license(repo, current_user, file, public_url_base)
	return SuccessResponse(
		message="Medical license uploaded.",
		data=DoctorProfileResponse.model_validate(profile),
	)


@router.post(
	"/me/credentials/submit",
	response_model=SuccessResponse[DoctorProfileResponse],
	status_code=status.HTTP_200_OK,
)
async def submit_verification(
	current_user: DoctorUser,
	session: DBSession,
) -> SuccessResponse[DoctorProfileResponse]:
	"""Submit all credentials for admin review — sets status to PENDING_REVIEW."""
	repo = _profile_repo(session)
	profile = await submit_for_verification(repo, current_user)
	return SuccessResponse(
		message="Submitted for verification. You will be notified within 24 hours.",
		data=DoctorProfileResponse.model_validate(profile),
	)


# ── Profile read ──────────────────────────────────────────────────────────────


@router.get(
	"/me/profile",
	response_model=SuccessResponse[DoctorProfileResponse],
)
async def get_profile(
	current_user: DoctorUser,
	session: DBSession,
) -> SuccessResponse[DoctorProfileResponse]:
	"""Get the current doctor's profile and verification status."""
	repo = _profile_repo(session)
	profile = await repo.get_or_create(current_user.id)
	return SuccessResponse(
		message="OK",
		data=DoctorProfileResponse.model_validate(profile),
	)
