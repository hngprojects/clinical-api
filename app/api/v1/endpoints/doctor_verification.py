import logging
from datetime import datetime, timezone
from uuid import UUID

import jwt
from fastapi import APIRouter, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession, DoctorUser, DoctorVerificationRepo
from app.core.config import get_settings
from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.core.responses import SuccessResponse
from app.models.doctor_verification import (
	DoctorVerification,
	DoctorVerificationAuditLog,
	DoctorVerificationDocument,
	DoctorVerificationStatus,
)
from app.models.user import User, UserRole
from app.schemas.doctor_verification import DoctorVerificationResponse, SignedUrlResponse
from app.services.private_storage import (
	delete_private_file,
	generate_document_signed_url,
	upload_private_file,
)
from app.tasks.emails import send_verification_status_email_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/doctors/verification", tags=["doctor-verification"])

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_MIME_TYPES = frozenset({"application/pdf", "image/jpeg", "image/png"})


async def _read_and_validate_file(file: UploadFile) -> bytes:
	if file.content_type not in ALLOWED_MIME_TYPES:
		raise BadRequestError(f"Unsupported file type for {file.filename}. Accepted types: PDF, JPG, PNG.")
	contents = await file.read()
	if len(contents) > MAX_FILE_SIZE:
		raise BadRequestError(f"File {file.filename} exceeds the maximum size limit of 10MB.")
	return contents


async def _save_uploaded_documents(
	verification_id: UUID,
	repo: DoctorVerificationRepo,
	medical_license: UploadFile,
	government_id: UploadFile,
	board_certifications: list[UploadFile] | None = None,
) -> list[DoctorVerificationDocument]:
	saved_docs = []

	try:
		# 1. Medical License
		ml_data = await _read_and_validate_file(medical_license)
		ml_upload = await upload_private_file(
			ml_data,
			medical_license.filename or "medical_license",
			medical_license.content_type or "application/octet-stream",
		)
		ml_doc = DoctorVerificationDocument(
			verification_id=verification_id,
			document_type="medical_license",
			file_path=ml_upload["file_path"],
			storage_type=ml_upload["storage_type"],
			filename=ml_upload["filename"],
			file_size=ml_upload["file_size"],
			mime_type=ml_upload["mime_type"],
		)
		repo.add_document(ml_doc)
		saved_docs.append(ml_doc)

		# 2. Government ID
		gid_data = await _read_and_validate_file(government_id)
		gid_upload = await upload_private_file(
			gid_data,
			government_id.filename or "government_id",
			government_id.content_type or "application/octet-stream",
		)
		gid_doc = DoctorVerificationDocument(
			verification_id=verification_id,
			document_type="government_id",
			file_path=gid_upload["file_path"],
			storage_type=gid_upload["storage_type"],
			filename=gid_upload["filename"],
			file_size=gid_upload["file_size"],
			mime_type=gid_upload["mime_type"],
		)
		repo.add_document(gid_doc)
		saved_docs.append(gid_doc)

		# 3. Board Certifications (optional, one-to-many)
		if board_certifications:
			for bc_file in board_certifications:
				# Skip empty files
				if not bc_file.filename:
					continue
				bc_data = await _read_and_validate_file(bc_file)
				bc_upload = await upload_private_file(
					bc_data, bc_file.filename, bc_file.content_type or "application/octet-stream"
				)
				bc_doc = DoctorVerificationDocument(
					verification_id=verification_id,
					document_type="board_certification",
					file_path=bc_upload["file_path"],
					storage_type=bc_upload["storage_type"],
					filename=bc_upload["filename"],
					file_size=bc_upload["file_size"],
					mime_type=bc_upload["mime_type"],
				)
				repo.add_document(bc_doc)
				saved_docs.append(bc_doc)

		await repo.flush()
		return saved_docs

	except Exception as exc:
		# Cleanup already uploaded files in storage on failure
		for doc in saved_docs:
			await delete_private_file(doc.file_path, doc.storage_type)
		raise exc


@router.post("", response_model=SuccessResponse[DoctorVerificationResponse], status_code=status.HTTP_201_CREATED)
async def submit_verification(
	current_user: DoctorUser,
	repo: DoctorVerificationRepo,
	license_number: str = Form(...),
	issuing_state: str = Form(...),
	license_expiry_date: str = Form(...),
	specialty: str = Form(...),
	medical_license: UploadFile = File(...),
	government_id: UploadFile = File(...),
	board_certifications: list[UploadFile] = File(default=None),
) -> SuccessResponse[DoctorVerificationResponse]:
	"""Submit professional credentials for verification (multipart/form-data)."""
	# Check for duplicate/pending requests (row locking user verification)
	existing = await repo.get_by_user_id(current_user.id, lock=True)
	if existing:
		if existing.status in (DoctorVerificationStatus.PENDING, DoctorVerificationStatus.APPROVED):
			raise ConflictError("A verification request is already pending or approved.")
		if existing.status == DoctorVerificationStatus.REJECTED:
			raise ConflictError("A rejected verification exists; use the resubmit endpoint instead.")

	# Validate dates
	try:
		expiry_dt = datetime.fromisoformat(license_expiry_date)
	except ValueError as exc:
		raise BadRequestError("Invalid date format for license_expiry_date. Use ISO format (YYYY-MM-DD).") from exc

	verification = DoctorVerification(
		user_id=current_user.id,
		license_number=license_number.strip(),
		issuing_state=issuing_state.strip(),
		license_expiry_date=expiry_dt,
		specialty=specialty.strip(),
		status=DoctorVerificationStatus.PENDING,
	)
	repo.add(verification)
	await repo.flush()

	# Save documents — each doc is registered via repo.add_document() inside the helper
	# and will be loaded onto verification after repo.refresh() via the selectin relationship.
	await _save_uploaded_documents(verification.id, repo, medical_license, government_id, board_certifications)

	# Create Audit Log
	audit = DoctorVerificationAuditLog(
		verification_id=verification.id,
		status_before="not_submitted",
		status_after="pending",
		changed_by=current_user.id,
	)
	repo.add_audit_log(audit)

	await repo.commit()
	await repo.refresh(verification)

	# Trigger notification email (non-blocking)
	try:
		send_verification_status_email_task.delay(
			to_email=current_user.email,
			first_name=current_user.first_name,
			status="pending",
		)
	except Exception:
		logger.exception("Failed to dispatch submit email notification for %s", current_user.email)

	return SuccessResponse(
		message="Credentials submitted successfully.",
		data=DoctorVerificationResponse.model_validate(verification),
	)


@router.get("/status", response_model=SuccessResponse[DoctorVerificationResponse])
async def get_verification_status(
	current_user: DoctorUser,
	repo: DoctorVerificationRepo,
) -> SuccessResponse[DoctorVerificationResponse]:
	"""Fetch current status, rejection reason, and timestamps for the authenticated doctor."""
	verification = await repo.get_by_user_id(current_user.id)
	if not verification:
		# Return a mock not_submitted payload to prevent frontend from hitting ambiguous state
		mock_verif = DoctorVerification(
			id=UUID(int=0),
			user_id=current_user.id,
			license_number="",
			issuing_state="",
			license_expiry_date=datetime.now(timezone.utc),
			specialty="",
			status=DoctorVerificationStatus.NOT_SUBMITTED,
			created_at=datetime.now(timezone.utc),
			updated_at=datetime.now(timezone.utc),
			documents=[],
		)
		return SuccessResponse(
			message="No verification record found.",
			data=DoctorVerificationResponse.model_validate(mock_verif),
		)

	return SuccessResponse(
		message="Verification status retrieved successfully.",
		data=DoctorVerificationResponse.model_validate(verification),
	)


@router.put("", response_model=SuccessResponse[DoctorVerificationResponse])
async def resubmit_verification(
	current_user: DoctorUser,
	repo: DoctorVerificationRepo,
	license_number: str = Form(...),
	issuing_state: str = Form(...),
	license_expiry_date: str = Form(...),
	specialty: str = Form(...),
	medical_license: UploadFile = File(...),
	government_id: UploadFile = File(...),
	board_certifications: list[UploadFile] = File(default=None),
) -> SuccessResponse[DoctorVerificationResponse]:
	"""Resubmit credentials after rejection."""
	verification = await repo.get_by_user_id(current_user.id, lock=True)
	if not verification:
		raise NotFoundError("No verification record found.")

	if verification.status != DoctorVerificationStatus.REJECTED:
		raise BadRequestError("Only rejected verification requests can be resubmitted.")

	# Validate dates
	try:
		expiry_dt = datetime.fromisoformat(license_expiry_date)
	except ValueError as exc:
		raise BadRequestError("Invalid date format for license_expiry_date. Use ISO format (YYYY-MM-DD).") from exc

	# Audit Log transition
	audit = DoctorVerificationAuditLog(
		verification_id=verification.id,
		status_before="rejected",
		status_after="pending",
		changed_by=current_user.id,
	)
	repo.add_audit_log(audit)

	# Update fields
	verification.license_number = license_number.strip()
	verification.issuing_state = issuing_state.strip()
	verification.license_expiry_date = expiry_dt
	verification.specialty = specialty.strip()
	verification.status = DoctorVerificationStatus.PENDING
	verification.rejection_reason = None
	verification.updated_at = datetime.now(timezone.utc)

	# Clean up old files from storage and database
	old_docs = list(verification.documents)
	for doc in old_docs:
		await delete_private_file(doc.file_path, doc.storage_type)
		await repo.delete_document(doc)
	await repo.flush()

	# Save new documents
	saved_docs = await _save_uploaded_documents(
		verification.id, repo, medical_license, government_id, board_certifications
	)
	verification.documents = saved_docs

	await repo.commit()
	await repo.refresh(verification)

	# Trigger notification email (non-blocking)
	try:
		send_verification_status_email_task.delay(
			to_email=current_user.email,
			first_name=current_user.first_name,
			status="pending",
		)
	except Exception:
		logger.exception("Failed to dispatch resubmit email notification for %s", current_user.email)

	return SuccessResponse(
		message="Credentials resubmitted successfully.",
		data=DoctorVerificationResponse.model_validate(verification),
	)


@router.patch("/status", response_model=SuccessResponse)
async def update_verification_status(
	current_user: CurrentUser,
	repo: DoctorVerificationRepo,
	session: DBSession,
	verification_id: UUID = Form(...),
	status_update: str = Form(...),  # 'approved' or 'rejected'
	rejection_reason: str | None = Form(default=None),
) -> SuccessResponse:
	"""Internal/admin endpoint to approve or reject a verification request."""
	# Enforce admin authorization
	if current_user.role != UserRole.ADMIN:
		raise ForbiddenError("Admin credentials required to update verification status.")

	verification = await repo.get_by_id(verification_id, lock=True)
	if not verification:
		raise NotFoundError("Verification record not found.")

	if verification.status != DoctorVerificationStatus.PENDING:
		raise BadRequestError("Only pending verification requests can be approved or rejected.")

	try:
		target_status = DoctorVerificationStatus(status_update.lower())
	except ValueError as exc:
		raise BadRequestError("Status update must be either 'approved' or 'rejected'.") from exc

	if target_status not in (DoctorVerificationStatus.APPROVED, DoctorVerificationStatus.REJECTED):
		raise BadRequestError("Status update must be either 'approved' or 'rejected'.")

	if target_status == DoctorVerificationStatus.REJECTED and not rejection_reason:
		raise BadRequestError("rejection_reason is required when rejecting a request.")

	status_before = verification.status.value
	verification.status = target_status
	verification.rejection_reason = rejection_reason.strip() if rejection_reason else None
	verification.updated_at = datetime.now(timezone.utc)

	# Create Audit Log
	audit = DoctorVerificationAuditLog(
		verification_id=verification.id,
		status_before=status_before,
		status_after=target_status.value,
		changed_by=current_user.id,
		rejection_reason=verification.rejection_reason,
	)
	repo.add_audit_log(audit)

	# If approved, promote user's role to DOCTOR
	if target_status == DoctorVerificationStatus.APPROVED:
		user_stmt = select(User).where(User.id == verification.user_id)
		res = await session.execute(user_stmt)
		verifying_user = res.scalar_one_or_none()
		if verifying_user:
			verifying_user.role = UserRole.DOCTOR

	await repo.commit()

	# Trigger notification email to verified doctor (non-blocking)
	user_stmt = select(User).where(User.id == verification.user_id)
	res = await session.execute(user_stmt)
	target_user = res.scalar_one_or_none()
	if target_user:
		try:
			send_verification_status_email_task.delay(
				to_email=target_user.email,
				first_name=target_user.first_name,
				status=target_status.value,
				rejection_reason=verification.rejection_reason,
			)
		except Exception:
			logger.exception("Failed to dispatch status email notification for %s", target_user.email)

	return SuccessResponse(message=f"Verification status successfully updated to {target_status.value}.")


@router.get("/documents/{documentId}", response_model=SuccessResponse[SignedUrlResponse])
async def get_document_signed_url(
	documentId: UUID,
	current_user: CurrentUser,
	repo: DoctorVerificationRepo,
) -> SuccessResponse[SignedUrlResponse]:
	"""Generate and return a short-lived signed URL to view a specific uploaded document (authorized users only)."""
	doc = await repo.get_document_by_id(documentId)
	if not doc:
		raise NotFoundError("Document not found.")

	# Access control: Must be own record or an admin
	if current_user.role != UserRole.ADMIN and doc.verification.user_id != current_user.id:
		raise ForbiddenError("Unauthorized access to document.")

	signed_url = await generate_document_signed_url(
		doc.file_path, str(doc.id), doc.storage_type, expires_in_seconds=600
	)
	return SuccessResponse(
		message="Signed URL generated successfully.",
		data=SignedUrlResponse(signed_url=signed_url),
	)


@router.get("/documents/{documentId}/view")
async def view_local_document(
	documentId: UUID,
	repo: DoctorVerificationRepo,
	token: str = Query(...),
) -> FileResponse:
	"""View endpoint for local storage fallback that validates signature token."""
	settings = get_settings()

	try:
		payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
	except jwt.ExpiredSignatureError as exc:
		raise ForbiddenError("Signed URL has expired.") from exc
	except jwt.PyJWTError as exc:
		raise ForbiddenError("Invalid signed URL token.") from exc

	if payload.get("doc_id") != str(documentId):
		raise ForbiddenError("Token document mismatch.")

	doc = await repo.get_document_by_id(documentId)
	if not doc:
		raise NotFoundError("Document not found.")

	if doc.storage_type != "local":
		raise BadRequestError("Document is stored on remote cloud storage.")

	return FileResponse(doc.file_path, media_type=doc.mime_type, filename=doc.filename)


@router.post("/dismiss", response_model=SuccessResponse)
async def dismiss_verification_banner(
	current_user: DoctorUser,
	session: DBSession,
) -> SuccessResponse:
	"""Mark doctor verification banner as dismissed so it does not render again."""
	current_user.is_verification_dismissed = True
	await session.commit()
	return SuccessResponse(message="Verification status banner dismissed successfully.")
