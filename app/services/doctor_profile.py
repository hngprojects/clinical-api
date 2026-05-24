import logging
from datetime import datetime, timezone

from fastapi import UploadFile

from app.core.exceptions import BadRequestError, ForbiddenError
from app.models.doctor_profile import DoctorProfile, DoctorVerificationStatus
from app.models.user import User, UserRole
from app.repositories.doctor_profile import DoctorProfileRepository
from app.schemas.doctor_profile import CredentialsRequest, ProfessionalInfoRequest
from app.services.storage import upload_medical_file

logger = logging.getLogger(__name__)

_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
_ALLOWED_DOC_TYPES = {"application/pdf", "image/jpeg", "image/png"}
_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


def _require_doctor(user: User) -> None:
	if user.role != UserRole.DOCTOR:
		raise ForbiddenError("Only doctors can access this resource.")


async def save_professional_info(
	repo: DoctorProfileRepository,
	user: User,
	payload: ProfessionalInfoRequest,
) -> DoctorProfile:
	_require_doctor(user)
	profile = await repo.get_or_create(user.id)
	profile.specialization = payload.specialization
	profile.years_of_experience = payload.years_of_experience
	profile.hospital = payload.hospital
	await repo.commit()
	await repo.refresh(profile)
	return profile


async def save_passport_photo(
	repo: DoctorProfileRepository,
	user: User,
	file: UploadFile,
	public_url_base: str,
) -> DoctorProfile:
	_require_doctor(user)

	if file.content_type not in _ALLOWED_IMAGE_TYPES:
		raise BadRequestError("Passport photo must be JPEG, PNG, or WebP.")

	contents = await file.read()
	if len(contents) > _MAX_FILE_SIZE:
		raise BadRequestError("File size must be 5MB or smaller.")

	meta = await upload_medical_file(
		data=contents,
		filename=file.filename or "passport.jpg",
		content_type=file.content_type,
		public_url_base=public_url_base,
	)

	profile = await repo.get_or_create(user.id)
	profile.passport_photo_url = meta["file_url"]
	await repo.commit()
	await repo.refresh(profile)
	return profile


async def save_credentials(
	repo: DoctorProfileRepository,
	user: User,
	payload: CredentialsRequest,
) -> DoctorProfile:
	_require_doctor(user)
	profile = await repo.get_or_create(user.id)
	profile.mdcn_license_number = payload.mdcn_license_number
	profile.nin = payload.nin
	await repo.commit()
	await repo.refresh(profile)
	return profile


async def save_medical_license(
	repo: DoctorProfileRepository,
	user: User,
	file: UploadFile,
	public_url_base: str,
) -> DoctorProfile:
	_require_doctor(user)

	if file.content_type not in _ALLOWED_DOC_TYPES:
		raise BadRequestError("Medical license must be PDF, JPEG, or PNG.")

	contents = await file.read()
	if len(contents) > _MAX_FILE_SIZE:
		raise BadRequestError("File size must be 5MB or smaller.")

	meta = await upload_medical_file(
		data=contents,
		filename=file.filename or "license.pdf",
		content_type=file.content_type,
		public_url_base=public_url_base,
	)

	profile = await repo.get_or_create(user.id)
	profile.medical_license_url = meta["file_url"]
	await repo.commit()
	await repo.refresh(profile)
	return profile


async def submit_for_verification(
	repo: DoctorProfileRepository,
	user: User,
) -> DoctorProfile:
	"""Validate all required fields are present then set status to PENDING_REVIEW."""
	_require_doctor(user)
	profile = await repo.get_by_user_id(user.id)

	if profile is None:
		raise BadRequestError("Please complete your professional information first.")

	missing = []
	if not profile.specialization:
		missing.append("specialization")
	if profile.years_of_experience is None:
		missing.append("years of experience")
	if not profile.passport_photo_url:
		missing.append("passport photograph")
	if not profile.mdcn_license_number:
		missing.append("MDCN license number")
	if not profile.nin:
		missing.append("NIN")
	if not profile.medical_license_url:
		missing.append("medical license")

	if missing:
		raise BadRequestError(f"Missing required fields: {', '.join(missing)}.")

	if profile.verification_status == DoctorVerificationStatus.VERIFIED:
		raise BadRequestError("Your profile is already verified.")

	profile.verification_status = DoctorVerificationStatus.PENDING_REVIEW
	profile.submitted_at = datetime.now(timezone.utc)
	await repo.commit()
	await repo.refresh(profile)
	return profile
