from celery import Celery
import os

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "kids",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.services.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,              # ACK solo dopo completamento → retry on crash
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,     # no over-prefetching su task lunghi
    task_time_limit=120,              # hard kill dopo 2min
    task_soft_time_limit=90,          # SoftTimeLimitExceeded exception
)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def invia_fcm_task(self, destinatario_id: int, titolo: str, corpo: str, tipo: str, extra: dict):
    from app.database import SessionLocal
    from app.services.fcm_service import manda_notifica
    db = SessionLocal()
    try:
        manda_notifica(db, destinatario_id, titolo, corpo, tipo=tipo, extra=extra)
    except Exception as exc:
        raise self.retry(exc=exc)
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=2)
def notifica_follower_nuovo_post(self, autore_id: int, post_id: int):
    """Fan-out: manda notifica a tutti i follower di `autore_id`."""
    from app.database import SessionLocal
    from app.models.modelli import Follow, Utente
    from app.services.fcm_service import manda_notifica
    db = SessionLocal()
    try:
        autore = db.query(Utente).get(autore_id)
        follower_ids = [f.follower_id for f in autore.follower_rel]
        for fid in follower_ids:
            try:
                manda_notifica(
                    db, fid, "📸 Nuovo post!",
                    f"{autore.nome} ha pubblicato",
                    tipo="post", extra={"post_id": post_id, "mittente_id": autore_id},
                )
            except Exception as e:
                # non bloccare gli altri destinatari
                pass
    finally:
        db.close()

