import uuid

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.models.medical_case import MedicalCase, MedicalCaseStatus
from app.models.user import User, UserRole
from app.db.session import AsyncSessionLocal
from app.services.auth.tokens import create_access_token


def make_token(user_id: uuid.UUID) -> str:
    token, _ = create_access_token(user_id)
    return token


@pytest.fixture
async def db_user() -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"wstest_{uuid.uuid4().hex[:8]}@clinsights.dev",
        first_name="WS",
        last_name="Tester",
        role=UserRole.PATIENT,
        is_active=True,
        is_email_verified=True,
    )
    async with AsyncSessionLocal() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    yield user
    async with AsyncSessionLocal() as session:
        existing = await session.get(User, user.id)
        if existing:
            await session.delete(existing)
            await session.commit()


@pytest.fixture
async def db_case(db_user: User) -> MedicalCase:
    case = MedicalCase(
        id=uuid.uuid4(),
        user_id=db_user.id,
        status=MedicalCaseStatus.COMPLETE,
    )
    async with AsyncSessionLocal() as session:
        session.add(case)
        await session.commit()
        await session.refresh(case)
    yield case
    async with AsyncSessionLocal() as session:
        existing = await session.get(MedicalCase, case.id)
        if existing:
            await session.delete(existing)
            await session.commit()


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

