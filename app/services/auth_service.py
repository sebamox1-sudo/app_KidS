from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
import bcrypt
import secrets
from dotenv import load_dotenv
import os

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "chiave_di_sviluppo_cambiala")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

# ── Durate token ─────────────────────────────────────────────
# Access token: breve durata — se rubato, scade presto
ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")  # 1 ora
)
# Refresh token: lunga durata — usato solo per rinnovare l'access token
REFRESH_TOKEN_EXPIRE_DAYS = int(
    os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30")  # 30 giorni
)

# ============================================================
# PASSWORD
# ============================================================
def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

def verifica_password(password: str, hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hash.encode("utf-8"))
    except Exception:
        return False

# ============================================================
# ACCESS TOKEN JWT — scade in 1 ora
# ============================================================
def crea_token(dati: dict, scadenza: Optional[timedelta] = None) -> str:
    payload = dati.copy()
    payload["tipo"] = "access"
    expire = datetime.now(timezone.utc) + (
        scadenza or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload["exp"] = expire
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decodifica_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        # Verifica che sia un access token, non un refresh token
        if payload.get("tipo") != "access":
            return None
        return payload
    except JWTError:
        return None

# ============================================================
# REFRESH TOKEN — stringa casuale opaca salvata nel DB
# Non è un JWT — è un token opaco difficile da indovinare
# ============================================================
def crea_refresh_token() -> str:
    # 64 byte casuali → 128 caratteri hex → praticamente impossibile da indovinare
    return secrets.token_hex(64)

def scadenza_refresh_token() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)