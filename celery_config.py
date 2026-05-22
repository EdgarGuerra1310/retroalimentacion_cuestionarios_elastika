from celery import Celery

celery_app = Celery(
    "retroalimentacion",
    broker="redis://localhost:6379/2",
    backend="redis://localhost:6379/2"
    include=["tasks"]   # 👈 CLAVE
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Lima",
    enable_utc=True
)

