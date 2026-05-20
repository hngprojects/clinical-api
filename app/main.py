from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from app.api.v1.router import api_router
from app.core.celery_app import configure_celery
from app.core.config import get_settings
from app.core.exceptions import (
	http_exception_handler,
	unhandled_exception_handler,
	validation_exception_handler,
)
from app.services.events import EventBus
from app.services.websocket import ConnectionRegistry

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
	configure_celery()

	event_bus = EventBus(settings.CELERY_BROKER_URL)
	await event_bus.connect()
	app.state.event_bus = event_bus

	connection_registry = ConnectionRegistry()
	app.state.connection_registry = connection_registry

	yield

	await event_bus.disconnect()


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

media_dir = Path(settings.MEDIA_DIR)
media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=media_dir), name="media")

# CORS
app.add_middleware(
	CORSMiddleware,
	allow_origins=settings.CORS_ORIGINS,
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)

# Exception handlers
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

# Routers
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/")
def root() -> dict[str, str]:
	return {"message": f"{settings.PROJECT_NAME} is running"}
