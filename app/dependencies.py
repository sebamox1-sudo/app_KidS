from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.auth_service import decodifica_token
from app.models.modelli import Utente

security = HTTPBearer()

def get_utente_corrente(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> Utente:
    """Dipendenza che estrae l'utente dal token JWT."""
    token = credentials.credentials
    payload = decodifica_token(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token non valido o scaduto",
            headers={"WWW-Authenticate": "Bearer"},
        )

    utente_id = payload.get("sub")
    if utente_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token malformato",
        )

    utente = db.query(Utente).filter(Utente.id == int(utente_id)).first()
    if utente is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utente non trovato",
        )

    return utente
