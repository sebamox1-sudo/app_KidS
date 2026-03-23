from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
import bcrypt
from dotenv import load_dotenv
import os

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "chiave_di_sviluppo_cambiala")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "43200"))

# ============================================================
# PASSWORD — usa bcrypt direttamente (compatibile Python 3.13)
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
# JWT TOKEN
# ============================================================
def crea_token(dati: dict, scadenza: Optional[timedelta] = None) -> str:
    payload = dati.copy()
    expire = datetime.now(timezone.utc) + (
        scadenza or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload["exp"] = expire
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decodifica_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None