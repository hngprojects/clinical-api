from celery import Celery
from celery.signals import worker_process_init

EMAIL_QUEUE = "email"

celery_app = Celery("clinsights")

PIPELINE_QUEUE = "pipeline"

celery_app.conf.update(
	task_acks_late=True,
	worker_prefetch_multiplier=1,
	task_default_queue="default",
	task_routes={
		"app.tasks.email.*": {"queue": EMAIL_QUEUE},
		"app.tasks.pipeline.*": {"queue": PIPELINE_QUEUE},
	},
	include=["app.tasks.email", "app.tasks.pipeline"],
)


def configure_celery(**kwargs: object) -> None:  # noqa: ARG001
	"""Configure broker and result backend from settings.

	Idempotent — safe to call multiple times.
	Call once during app/worker startup before dispatching tasks.
	"""
	if celery_app.conf.broker_url:
		return
	from app.core.config import get_settings

	settings = get_settings()
	celery_app.conf.update(
		broker_url=settings.CELERY_BROKER_URL,
		result_backend=settings.CELERY_RESULT_BACKEND,
	)


celery_app.on_after_finalize.connect(configure_celery)


@worker_process_init.connect
def dispose_inherited_db_connections(**kwargs: object) -> None:  # noqa: ARG001
	"""Drop DB connections inherited from the Celery parent process on fork.

	Connections are bound to the parent's event loop; the persistent worker
	loop created in pipeline.py will open fresh ones on first use.
	"""
	from app.db.session import engine

	engine.sync_engine.dispose()
