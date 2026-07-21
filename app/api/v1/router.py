from fastapi import APIRouter

from app.api.v1.endpoints import (
	ai_interpretation,
	auth,
	chat,
	contact,
	doctors,
	doctor_verification,
	export,
	guest_cases,
	guest_session,
	health,
	lab_result,
	medical_case,
	notification,
	subscribe,
	users,
	waitlist,
	ws_chat,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router)
api_router.include_router(guest_session.router)
api_router.include_router(guest_cases.router)
api_router.include_router(medical_case.router)
api_router.include_router(lab_result.router)
api_router.include_router(ai_interpretation.router)
api_router.include_router(chat.router)
api_router.include_router(notification.router)
api_router.include_router(waitlist.router)
api_router.include_router(contact.router)
api_router.include_router(subscribe.router)
api_router.include_router(export.router)
api_router.include_router(users.router)
api_router.include_router(doctors.router)
api_router.include_router(doctor_verification.router)
api_router.include_router(ws_chat.router)
