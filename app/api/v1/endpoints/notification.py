import json
import logging
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps import ConnectionRegistryDep, CurrentUser, EventBusDep, NotificationRepo, UserRepo
from app.core.responses import SuccessResponse
from app.db.session import AsyncSessionLocal
from app.repositories.notification import NotificationRepository
from app.schemas.notification import (
	NotificationPreferencesResponse,
	NotificationPreferencesUpdate,
	NotificationResponse,
)
from app.services.notification import (
	list_notifications,
	mark_as_read,
	unread_count,
	update_notification_preferences,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _format_sse(event: str, data: dict, notification_id: UUID) -> bytes:
	payload = json.dumps(data)
	return (f"id: {notification_id}\nevent: {event}\ndata: {payload}\n\n").encode("utf-8")


async def _stream_notifications(
	request: Request,
	event_bus: EventBusDep,
	notif_repo: NotificationRepo,
	current_user: CurrentUser,
	connection_registry: ConnectionRegistryDep,
	last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> AsyncIterator[bytes]:
	"""Stream pending notifications via SSE with live EventBus updates.

	Implementation notes:
	- Replay is performed using a short-lived session that is closed after replay.
	- Live events use a fresh session per incoming event to avoid holding DB connections for the lifetime of the SSE connection.
	- Keepalive pings are sent approximately every 30s.
	- We enforce a single active SSE per user via ConnectionRegistry.
	"""
	logger.debug("SSE generator started for user=%s", current_user.id)

	# Enforce single active stream per user
	if connection_registry.get_stream_count(current_user.id) >= 1:
		logger.info("SSE connection refused: active stream exists for user=%s", current_user.id)
		raise HTTPException(status_code=409, detail="Another SSE connection is already active for this user")

	# Register stream so other callers know there's an active connection
	connection_registry.register_stream(current_user.id)

	try:
		# Replay missed notifications using a single short-lived session
		last_notification = None
		if last_event_id:
			try:
				last_notification_id = UUID(last_event_id)
				async with AsyncSessionLocal() as session:
					repo = NotificationRepository(session)
					candidate = await repo.get_by_id(last_notification_id)
					if candidate is not None and candidate.user_id == current_user.id:
						last_notification = candidate
			except ValueError:
				last_notification = None

		if last_notification is not None:
			logger.debug("SSE starting replay loop for user=%s after %s", current_user.id, last_notification.id)
			async with AsyncSessionLocal() as session:
				repo = NotificationRepository(session)
				async for notification in repo.stream_after(
					current_user.id,
					created_after=last_notification.created_at,
					last_id=last_notification.id,
				):
					logger.debug("SSE replay candidate %s for user=%s", notification.id, current_user.id)
					if await request.is_disconnected():
						logger.info("SSE client disconnected during replay for user=%s", current_user.id)
						return
					await repo.mark_delivered(notification.id)
					await session.commit()
					await repo.refresh(notification)
					yield _format_sse(
						notification.type.value,
						{
							"id": str(notification.id),
							"title": notification.title,
							"message": notification.message,
							"data": notification.data,
							"delivered_at": notification.delivered_at.isoformat()
							if notification.delivered_at
							else None,
						},
						notification.id,
					)

		logger.debug("SSE entering live subscribe loop for user=%s", current_user.id)
		sub_iter = event_bus.subscribe(current_user.id)
		try:
			while True:
				try:
					event = await sub_iter.__anext__()
				except StopAsyncIteration:
					break

				if event is None:
					logger.debug("SSE yielding ping for user=%s", current_user.id)
					yield b": ping\n\n"
					continue

				if not isinstance(event, dict):
					logger.warning("SSE got non-dict event for user=%s", current_user.id)
					continue

				payload = event.get("payload")
				if not payload or not isinstance(payload, dict):
					logger.warning("SSE event missing payload for user=%s", current_user.id)
					continue

				# Validate payload size/data
				try:
					data_bytes = json.dumps(payload.get("data", {})).encode("utf-8")
				except Exception:
					data_bytes = b""

				if len(data_bytes) > 200_000:
					logger.warning("SSE event data too large; truncating for user=%s", current_user.id)
					payload["data"] = {"_truncated": True}

				notification_id = payload.get("notification_id")
				if not notification_id:
					logger.warning("SSE payload missing notification_id for user=%s", current_user.id)
					continue
				try:
					notification_uuid = UUID(str(notification_id))
				except ValueError:
					logger.warning("SSE invalid notification id in payload for user=%s", current_user.id)
					continue

				# Use a transient session for live event work to avoid holding a connection
				async with AsyncSessionLocal() as session2:
					repo2 = NotificationRepository(session2)
					notification = await repo2.get_by_id(notification_uuid)
					if notification is None or notification.user_id != current_user.id:
						logger.info(
							"SSE notification not found or wrong owner %s for user=%s",
							notification_uuid,
							current_user.id,
						)
						continue
					await repo2.mark_delivered(notification.id)
					await session2.commit()
					await repo2.refresh(notification)
					# Optionally validate event type matches notification.type
					event_type = event.get("type")
					if event_type and event_type != notification.type.value:
						logger.warning(
							"SSE event type mismatch for notification %s user=%s", notification.id, current_user.id
						)
					yield _format_sse(
						notification.type.value,
						{
							"id": str(notification.id),
							"title": notification.title,
							"message": notification.message,
							"data": notification.data,
							"delivered_at": notification.delivered_at.isoformat()
							if notification.delivered_at
							else None,
						},
						notification.id,
					)
		finally:
			try:
				aclose = getattr(sub_iter, "aclose", None)
				if aclose is not None:
					await aclose()
			except Exception:
				logger.exception("Error closing event subscription for user=%s", current_user.id)
	finally:
		# Unregister the stream so other clients can connect
		connection_registry.unregister_stream(current_user.id)


@router.get(
	"",
	response_model=SuccessResponse[list[NotificationResponse]],
)
async def list_mine(
	current_user: CurrentUser,
	notif_repo: NotificationRepo,
	unread_only: bool = Query(False),
	offset: int = Query(0, ge=0),
	limit: int = Query(50, ge=1, le=100),
) -> SuccessResponse[list[NotificationResponse]]:
	"""List notifications for the authenticated user."""
	notifs = await list_notifications(
		notif_repo,
		current_user.id,
		unread_only=unread_only,
		offset=offset,
		limit=limit,
	)
	return SuccessResponse(
		message="OK",
		data=[NotificationResponse.model_validate(n) for n in notifs],
	)


@router.get(
	"/unread-count",
	response_model=SuccessResponse[dict],
)
async def get_unread_count(
	current_user: CurrentUser,
	notif_repo: NotificationRepo,
) -> SuccessResponse[dict]:
	"""Return the number of unread notifications."""
	count = await unread_count(notif_repo, current_user.id)
	return SuccessResponse(message="OK", data={"unread": count})


@router.patch(
	"/{notification_id}/read",
	response_model=SuccessResponse[NotificationResponse],
)
async def read(
	notification_id: UUID,
	current_user: CurrentUser,
	notif_repo: NotificationRepo,
) -> SuccessResponse[NotificationResponse]:
	"""Mark a single notification as read."""
	notif = await mark_as_read(notif_repo, notification_id, user=current_user)
	return SuccessResponse(
		message="Notification marked as read.",
		data=NotificationResponse.model_validate(notif),
	)


@router.get(
	"/preferences",
	response_model=SuccessResponse[NotificationPreferencesResponse],
)
async def get_preferences(
	current_user: CurrentUser,
) -> SuccessResponse[NotificationPreferencesResponse]:
	"""Retrieve the authenticated user's notification preferences."""
	return SuccessResponse(
		message="OK",
		data=NotificationPreferencesResponse.model_validate(current_user),
	)


@router.patch(
	"/preferences",
	response_model=SuccessResponse[NotificationPreferencesResponse],
)
async def update_preferences(
	current_user: CurrentUser,
	user_repo: UserRepo,
	payload: NotificationPreferencesUpdate,
) -> SuccessResponse[NotificationPreferencesResponse]:
	"""Update the authenticated user's notification preferences."""
	user = await update_notification_preferences(user_repo, current_user, payload)
	return SuccessResponse(
		message="Preferences updated.",
		data=NotificationPreferencesResponse.model_validate(user),
	)


@router.get("/stream")
async def stream_notifications(
	request: Request,
	current_user: CurrentUser,
	notif_repo: NotificationRepo,
	event_bus: EventBusDep,
	connection_registry: ConnectionRegistryDep,
	last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
	"""Open a server-sent events stream for authenticated notifications."""
	return StreamingResponse(
		_stream_notifications(
			request,
			event_bus,
			notif_repo,
			current_user,
			connection_registry,
			last_event_id,
		),
		media_type="text/event-stream",
		headers={
			"Cache-Control": "no-cache",
			"Connection": "keep-alive",
			"X-Accel-Buffering": "no",
		},
	)
