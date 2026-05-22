import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.models.medical_case import MedicalCase, MedicalCaseStatus
from app.models.user import User, UserRole
from app.core.config import get_settings
from app.services.auth.tokens import create_access_token


def make_token(user_id: uuid.UUID) -> str:
    token, _ = create_access_token(user_id)
    return token


# ── Helpers that run DB work in their own isolated event loop.
#    Each call creates a fresh engine scoped to that loop, avoiding
#    the asyncpg "Future attached to a different loop" error.

def _run(coro):
    """Run a coroutine in a brand-new event loop, then close it."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_session_factory():
    """Create a fresh async engine + sessionmaker for the current event loop."""
    engine = create_async_engine(str(get_settings().DATABASE_URL), echo=False)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


async def _create_user() -> User:
    factory, engine = _make_session_factory()
    user = User(
        id=uuid.uuid4(),
        email=f"wstest_{uuid.uuid4().hex[:8]}@clinsights.dev",
        first_name="WS",
        last_name="Tester",
        role=UserRole.PATIENT,
        is_active=True,
        is_email_verified=True,
    )
    async with factory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    await engine.dispose()
    return user


async def _delete_user(user_id: uuid.UUID) -> None:
    factory, engine = _make_session_factory()
    async with factory() as session:
        existing = await session.get(User, user_id)
        if existing:
            await session.delete(existing)
            await session.commit()
    await engine.dispose()


async def _create_case(user_id: uuid.UUID) -> MedicalCase:
    factory, engine = _make_session_factory()
    case = MedicalCase(
        id=uuid.uuid4(),
        user_id=user_id,
        status=MedicalCaseStatus.COMPLETE,
    )
    async with factory() as session:
        session.add(case)
        await session.commit()
        await session.refresh(case)
    await engine.dispose()
    return case


async def _delete_case(case_id: uuid.UUID) -> None:
    factory, engine = _make_session_factory()
    async with factory() as session:
        existing = await session.get(MedicalCase, case_id)
        if existing:
            await session.delete(existing)
            await session.commit()
    await engine.dispose()


# ── Fixtures: synchronous so they never touch pytest-asyncio's event loop
#    or TestClient's anyio loop. Each DB call gets its own fresh loop + engine.

@pytest.fixture
def db_user() -> User:
    user = _run(_create_user())
    yield user
    _run(_delete_user(user.id))


@pytest.fixture
def db_case(db_user: User) -> MedicalCase:
    case = _run(_create_case(db_user.id))
    yield case
    _run(_delete_case(case.id))


# ── Tests

class TestWebSocketAuth:
    def test_invalid_token_closes_connection(self):
        closed = False
        try:
            with TestClient(app) as client:
                with client.websocket_connect("/api/v1/ws/chat") as ws:
                    ws.send_json({
                        "type": "init",
                        "case_id": str(uuid.uuid4()),
                        "token": "this.is.not.a.valid.jwt",
                    })
                    while True:
                        ws.receive_json()
        except WebSocketDisconnect:
            closed = True
        assert closed

    def test_malformed_init_closes_connection(self):
        closed = False
        try:
            with TestClient(app) as client:
                with client.websocket_connect("/api/v1/ws/chat") as ws:
                    ws.send_json({"type": "message", "content": "hello"})
                    while True:
                        ws.receive_json()
        except WebSocketDisconnect:
            closed = True
        assert closed

    def test_missing_case_id_field_closes_connection(self):
        closed = False
        try:
            with TestClient(app) as client:
                with client.websocket_connect("/api/v1/ws/chat") as ws:
                    ws.send_json({"type": "init", "token": "sometoken"})
                    while True:
                        ws.receive_json()
        except WebSocketDisconnect:
            closed = True
        assert closed


class TestWebSocketCaseOwnership:
    def test_valid_token_nonexistent_case_closes_connection(
        self, db_user: User
    ):
        token = make_token(db_user.id)
        closed = False
        try:
            with TestClient(app) as client:
                with client.websocket_connect("/api/v1/ws/chat") as ws:
                    ws.send_json({
                        "type": "init",
                        "case_id": str(uuid.uuid4()),
                        "token": token,
                    })
                    while True:
                        ws.receive_json()
        except WebSocketDisconnect:
            closed = True
        assert closed

    def test_valid_token_valid_case_stays_open(
        self, db_user: User, db_case: MedicalCase
    ):
        token = make_token(db_user.id)
        history_received = False
        disconnect_code = None

        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                with client.websocket_connect("/api/v1/ws/chat") as ws:
                    ws.send_json({
                        "type": "init",
                        "case_id": str(db_case.id),
                        "token": token,
                    })
                    msg = ws.receive_json()
                    print(f"\nReceived message: {msg}")
                    if msg.get("type") == "history":
                        history_received = True
                        assert isinstance(msg["messages"], list)
        except WebSocketDisconnect as e:
            disconnect_code = e.code
            print(f"\nDisconnected with code={e.code} reason={e.reason}")
        except Exception as e:
            print(f"\nException: {type(e).__name__}: {e}")

        assert history_received, f"Expected history message, got disconnect code={disconnect_code}"


class TestWebSocketMessaging:
    def test_ping_returns_pong(self, db_user: User, db_case: MedicalCase):
        token = make_token(db_user.id)
        pong_received = False

        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                with client.websocket_connect("/api/v1/ws/chat") as ws:
                    ws.send_json({
                        "type": "init",
                        "case_id": str(db_case.id),
                        "token": token,
                    })
                    msg = ws.receive_json()
                    print(f"\nInit response: {msg}")
                    if msg.get("type") == "history":
                        ws.send_json({"type": "ping"})
                        msg2 = ws.receive_json()
                        print(f"\nPing response: {msg2}")
                        if msg2.get("type") == "pong":
                            pong_received = True
        except WebSocketDisconnect as e:
            print(f"\nDisconnected: code={e.code}")
        except Exception as e:
            print(f"\nException: {type(e).__name__}: {e}")

        assert pong_received

    def test_unknown_type_gets_error_response(
        self, db_user: User, db_case: MedicalCase
    ):
        token = make_token(db_user.id)
        error_received = False

        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                with client.websocket_connect("/api/v1/ws/chat") as ws:
                    ws.send_json({
                        "type": "init",
                        "case_id": str(db_case.id),
                        "token": token,
                    })
                    msg = ws.receive_json()
                    if msg.get("type") == "history":
                        ws.send_json({"type": "banana"})
                        msg2 = ws.receive_json()
                        print(f"\nUnknown type response: {msg2}")
                        if msg2.get("type") == "error" and msg2.get("code") == "UNKNOWN_TYPE":
                            error_received = True
        except WebSocketDisconnect as e:
            print(f"\nDisconnected: code={e.code}")
        except Exception as e:
            print(f"\nException: {type(e).__name__}: {e}")

        assert error_received

    def test_empty_content_gets_validation_error(
        self, db_user: User, db_case: MedicalCase
    ):
        token = make_token(db_user.id)
        error_received = False

        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                with client.websocket_connect("/api/v1/ws/chat") as ws:
                    ws.send_json({
                        "type": "init",
                        "case_id": str(db_case.id),
                        "token": token,
                    })
                    msg = ws.receive_json()
                    if msg.get("type") == "history":
                        ws.send_json({"type": "message", "content": "   "})
                        msg2 = ws.receive_json()
                        print(f"\nValidation response: {msg2}")
                        if msg2.get("type") == "error" and msg2.get("code") == "VALIDATION_ERROR":
                            error_received = True
        except WebSocketDisconnect as e:
            print(f"\nDisconnected: code={e.code}")
        except Exception as e:
            print(f"\nException: {type(e).__name__}: {e}")

        assert error_received