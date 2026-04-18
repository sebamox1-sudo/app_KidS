from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from dotenv import load_dotenv
from urllib.parse import urlparse
import os
import logging

load_dotenv(override=False)

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]  # crash-fast se manca

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

try:
    parsed = urlparse(DATABASE_URL)
    logger.info(f"DB: connessione a {parsed.hostname}{parsed.path}")
except Exception:
    logger.info("DB: URL configurata")


# ============================================================
# POOL DIMENSIONATO PER 500 UTENTI CONCORRENTI
# ============================================================
# Assunzione: 4 worker Gunicorn × pool_size 10 = 40 connessioni base
#             + max_overflow 10 per worker = max 80 connessioni totali
# 
# Railway Postgres (Hobby plan): limite ~100 connessioni.
# Riservare ~20 per migration/admin/scheduler = 80 disponibili per l'app.
#
# Se passi a Postgres Pro (500 connessioni) puoi alzare pool_size a 20.
# ============================================================

POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))  # s di attesa per una connessione
POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))  # 30 min — evita connessioni stale

engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT,
    pool_recycle=POOL_RECYCLE,
    pool_pre_ping=True,  # testa la connessione prima di usarla — fix Railway idle disconnect
    connect_args={
        # Timeout lato client: se una query supera 10s, abortisce invece di bloccare il pool
        "options": "-c statement_timeout=10000",
        "connect_timeout": 10,
        "application_name": "kids-api",
    },
    echo=False,
)


# Logging utile per debugging sotto carico
@event.listens_for(engine, "connect")
def _on_connect(dbapi_conn, connection_record):
    logger.debug("DB: nuova connessione aperta")


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,  # evita refetch post-commit inutili
)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()