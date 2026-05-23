import logging

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.responses import ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)


class ServerError(HTTPException):
	"""Raised when an error is caught."""

	def __init__(self, message: str = "Server error") -> None:
		super().__init__(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=message)


class BadGatewayError(HTTPException):
	"""Raised when an error occur when interacting with a external service."""

	def __init__(self, message: str = "Gateway error") -> None:
		super().__init__(status_code=status.HTTP_502_BAD_GATEWAY, detail=message)


class EmailError(HTTPException):
	"""Raised when an email error occurs."""

	def __init__(self, message: str = "Email error") -> None:
		super().__init__(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=message)


class NotFoundError(HTTPException):
	"""Raised when a requested resource does not exist."""

	def __init__(self, message: str = "Resource not found") -> None:
		super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=message)


class UnauthorizedError(HTTPException):
	"""Raised when a user is not authenticated."""

	def __init__(self, message: str = "Unauthorized") -> None:
		super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=message)


class ForbiddenError(HTTPException):
	"""Raised when a user does not have permission to perform an action."""

	def __init__(self, message: str = "Forbidden") -> None:
		super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=message)


class GuestLimitExceeded(ForbiddenError):
	"""Raised when a guest session has exhausted chat or upload quota."""

	def __init__(self, message: str = "Guest usage limit reached.") -> None:
		super().__init__(message)


class RateLimitExceeded(HTTPException):
	"""Raised when a client exceeds a configured request rate limit."""

	def __init__(self, message: str = "Too many requests. Please try again later.") -> None:
		super().__init__(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=message)


class ConflictError(HTTPException):
	"""Raised when a resource already exists."""

	def __init__(self, message: str = "Conflict") -> None:
		super().__init__(status_code=status.HTTP_409_CONFLICT, detail=message)


class BadRequestError(HTTPException):
	"""Raised when a request payload or state is invalid."""

	def __init__(self, message: str = "Bad request") -> None:
		super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
	"""Handles all HTTPExceptions and returns a consistent error response."""
	return JSONResponse(
		status_code=exc.status_code,
		content=ErrorResponse(message=str(exc.detail)).model_dump(),
	)


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
	"""Handles Pydantic validation errors and returns field-level error details."""
	errors = [
		ErrorDetail(
			field=" -> ".join(str(loc) for loc in error["loc"] if loc != "body"),
			message=error["msg"].removeprefix("Value error, "),
		)
		for error in exc.errors()
	]
	top_message = errors[0].message if errors else "Validation error"
	return JSONResponse(
		status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
		content=ErrorResponse(message=top_message, errors=errors).model_dump(),
	)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
	"""Catches any unhandled exceptions and returns a generic 500 response."""
	logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
	return JSONResponse(
		status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
		content=ErrorResponse(message="An unexpected error occurred. Please try again later.").model_dump(),
	)
