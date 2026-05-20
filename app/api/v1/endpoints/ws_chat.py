"""
Connection lifecycle:
  1. Client connects
  2. Client sends init within INIT_TIMEOUT_SECONDS:
         {"type": "init", "case_id": "<uuid>", "token": "<jwt>"}
  3. Server validates token, checks case ownership, registers connection
  4. Server sends last 50 messages as history
  5. Client sends messages: {"type": "message", "content": "..."}
  6. Server streams AI response token by token, saves to DB when done
  7. Client disconnects → server cleans up registry

Message ordering:
  Each connection gets an asyncio.Queue. Incoming messages are enqueued
  immediately. A single consumer coroutine drains the queue in order.
  If the AI is mid-response when a new message arrives, it waits in the
  queue. Nothing is ever dropped.

Multi-device:
  ConnectionRegistry holds list[WebSocket] per user_id. The "done" event
  is broadcast to all of the user's active connections so both devices
  see the completed message simultaneously.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.db.session import AsyncSessionLocal
from app.repositories.ai_interpretation import AIInterpretationRepository
from app.repositories.chat import ChatRepository
from app.repositories.lab_result import LabResultRepository
from app.repositories.medical_case import MedicalCaseRepository
from app.repositories.token_blocklist import TokenBlocklistRepository
from app.repositories.user import UserRepository
from app.schemas.ws_chat import (
	InitMessage,
	UserMessage,
	make_done,
	make_error,
	make_history,
	make_pong,
	make_queued,
	make_token,
)
from app.services.auth.tokens import decode_access_token
from app.services.websocket import ConnectionRegistry
from app.services.websocket_chat import (
	build_system_prompt,
	chat_to_wire,
	generate_ai_response,
	save_ai_message,
	save_user_message,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

INIT_TIMEOUT_SECONDS = 10


# ── Auth (manual — no Depends, WebSocket handshake has no Authorization header)
async def _authenticate(token: str, session) -> UUID | None:
	"""
	Validate JWT and return user_id if valid, else None.
	Returns None instead of raising so the caller can send a clean
	WebSocket error before closing.
	"""
	try:
		payload = decode_access_token(token)
	except jwt.ExpiredSignatureError:
		logger.warning("[ws_chat] expired token")
		return None
	except jwt.PyJWTError:
		logger.warning("[ws_chat] invalid token")
		return None

	jti = payload.get("jti")
	if not jti:
		return None

	if await TokenBlocklistRepository(session).is_revoked(jti):
		logger.warning("[ws_chat] revoked token jti=%s", jti)
		return None

	subject = payload.get("sub")
	if not subject:
		return None

	try:
		user_id = UUID(str(subject))
	except ValueError:
		return None

	user = await UserRepository(session).get_by_id(user_id)
	if user is None or not user.is_active or not user.is_email_verified:
		return None

	return user_id


# ── Per-message processor


async def _process_message(
	text: str,
	case_id: UUID,
	user_id: UUID,
	websocket: WebSocket,
	registry: ConnectionRegistry,
) -> None:
	"""
	Handle one user message end-to-end"""
	async with AsyncSessionLocal() as session:
		chat_repo = ChatRepository(session)
		interp_repo = AIInterpretationRepository(session)
		lab_repo = LabResultRepository(session)

		# 1. Save user message
		user_msg = await save_user_message(chat_repo, case_id, user_id, text)
		logger.info("[ws_chat] user message saved id=%s case=%s", user_msg.id, case_id)

		# 2. Load history (200 messages — trim_history handles token budget)
		history = await chat_repo.list_by_case(case_id, limit=200)

		# 3. Build system prompt
		system_prompt = await build_system_prompt(case_id, interp_repo, lab_repo)

		# 4. Stream AI response
		collected: list[str] = []
		try:
			async for token in generate_ai_response(system_prompt, history, text):
				collected.append(token)
				try:
					await websocket.send_json(make_token(token))
				except Exception:
					# Client disconnected mid-stream.
					# Keep collecting so we can still save the full response.
					logger.info("[ws_chat] client gone mid-stream, collecting remainder")

		except Exception as exc:
			logger.exception("[ws_chat] AI generation failed case=%s: %s", case_id, exc)
			try:
				await websocket.send_json(make_error("AI_ERROR", "Failed to generate response. Please try again."))
			except Exception:
				pass

		full_response = "".join(collected).strip()

		# 5. Save AI response to DB — always, even if client disconnected
		if full_response:
			ai_msg = await save_ai_message(chat_repo, case_id, full_response)
			logger.info("[ws_chat] AI message saved id=%s case=%s", ai_msg.id, case_id)
			# Broadcast done to ALL user connections (multi-device)
			await registry.broadcast(user_id, make_done(str(ai_msg.id)))
		else:
			try:
				await websocket.send_json(make_error("AI_ERROR", "No response received from AI provider."))
			except Exception:
				pass


# ── Queue consumer (runs for the lifetime of one connection)
async def _consume_queue(
	queue: asyncio.Queue[str | None],
	case_id: UUID,
	user_id: UUID,
	websocket: WebSocket,
	registry: ConnectionRegistry,
) -> None:
	"""
	Drain the message queue strictly in order, one at a time.
	Exits when it receives None (the shutdown sentinel).
	This guarantees messages are always processed sequentially —
	the next message only starts after the AI finishes the current one.
	"""
	while True:
		text = await queue.get()

		if text is None:
			queue.task_done()
			break

		try:
			await _process_message(text, case_id, user_id, websocket, registry)
		except Exception:
			logger.exception("[ws_chat] unhandled error in consumer case=%s", case_id)
			try:
				await websocket.send_json(make_error("UNKNOWN_ERROR", "An unexpected error occurred."))
			except Exception:
				pass
		finally:
			queue.task_done()


# ── Endpoint
@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket) -> None:
	await websocket.accept()

	registry: ConnectionRegistry = websocket.app.state.connection_registry

	# ── 1. Wait for init message
	try:
		raw = await asyncio.wait_for(
			websocket.receive_json(),
			timeout=INIT_TIMEOUT_SECONDS,
		)
	except asyncio.TimeoutError:
		await websocket.send_json(make_error("TIMEOUT", "Init message not received within 10 seconds."))
		await websocket.close(code=4008)
		return
	except WebSocketDisconnect:
		return

	# ── 2. Validate init message shape
	try:
		init = InitMessage.model_validate(raw)
	except Exception as exc:
		await websocket.send_json(make_error("VALIDATION_ERROR", str(exc)))
		await websocket.close(code=4000)
		return

	# ── 3. Authenticate and check case ownership
	async with AsyncSessionLocal() as session:
		user_id = await _authenticate(init.token, session)
		if user_id is None:
			await websocket.send_json(make_error("UNAUTHORIZED", "Invalid or expired token."))
			await websocket.close(code=4001)
			return

		case = await MedicalCaseRepository(session).get_by_id(init.case_id)
		if case is None:
			await websocket.send_json(make_error("NOT_FOUND", "Medical case not found."))
			await websocket.close(code=4004)
			return
		if case.user_id != user_id:
			await websocket.send_json(make_error("FORBIDDEN", "You do not have access to this case."))
			await websocket.close(code=4003)
			return

	case_id: UUID = init.case_id

	# ── 4. Register connection
	registry.connect(user_id, websocket)
	logger.info("[ws_chat] registered user=%s case=%s", user_id, case_id)

	# ── 5. Send history
	async with AsyncSessionLocal() as session:
		recent = await ChatRepository(session).get_recent_for_case(case_id, limit=50)
		await websocket.send_json(make_history([chat_to_wire(m) for m in recent]))
		logger.debug("[ws_chat] sent %d history messages user=%s", len(recent), user_id)

	# ── 6. Start queue consumer
	queue: asyncio.Queue[str | None] = asyncio.Queue()
	consumer = asyncio.create_task(_consume_queue(queue, case_id, user_id, websocket, registry))

	# ── 7. Receive loop
	try:
		while True:
			try:
				raw = await websocket.receive_json()
			except WebSocketDisconnect:
				logger.info("[ws_chat] disconnected user=%s case=%s", user_id, case_id)
				break
			except Exception as exc:
				logger.warning("[ws_chat] receive error user=%s: %s", user_id, exc)
				break

			msg_type = raw.get("type") if isinstance(raw, dict) else None

			if msg_type == "ping":
				await websocket.send_json(make_pong())

			elif msg_type == "message":
				try:
					user_msg = UserMessage.model_validate(raw)
				except Exception as exc:
					await websocket.send_json(make_error("VALIDATION_ERROR", str(exc)))
					continue

				position = queue.qsize()
				await queue.put(user_msg.content)

				if position > 0:
					await websocket.send_json(make_queued(position + 1))
					logger.debug("[ws_chat] queued position=%d user=%s", position + 1, user_id)

			else:
				await websocket.send_json(
					make_error(
						"UNKNOWN_TYPE",
						f"Unknown message type '{msg_type}'. Expected 'message' or 'ping'.",
					)
				)

	finally:
		# Shutdown — always runs even on crash
		await queue.put(None)  # sentinel to stop consumer
		try:
			await asyncio.wait_for(consumer, timeout=5.0)
		except asyncio.TimeoutError:
			logger.warning("[ws_chat] consumer timed out — cancelling user=%s", user_id)
			consumer.cancel()

		registry.disconnect(user_id, websocket)
		logger.info("[ws_chat] cleaned up user=%s case=%s", user_id, case_id)
