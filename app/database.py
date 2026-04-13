from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
import os
import logging

load_dotenv(override=False)

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "NESSUNA_URL_TROVATA")
# SICUREZZA: MAI stampare DATABASE_URL — contiene username e password.
# Logghiamo solo host e nome del database, zero credenziali.
try:
    from urllib.parse import urlparse
    parsed = urlparse(DATABASE_URL)
    logger.info(f"DB: connessione a {parsed.hostname}{parsed.path}")
except Exception:
    logger.info("DB: URL configurata (parsing non riuscito)")
 
# Railway dà postgres:// ma SQLAlchemy 2.0 vuole postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
 
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
 
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()