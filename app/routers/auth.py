from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone
from app.database import get_db
from app.models.modelli import Utente, Streak, Follow, Post, RefreshToken
from app.schemas.schemi import RegistrazioneRequest, LoginRequest, TokenResponse, UtenteResponse
from app.services.auth_service import (
    hash_password, verifica_password, crea_token,
    crea_refresh_token, scadenza_refresh_token,
)
from app.dependencies import get_utente_corrente

router = APIRouter(prefix="/auth", tags=["Auth"])


def _crea_token_coppia(utente: Utente, db: Session) -> tuple[str, str]:
    """Crea access token + refresh token e salva il refresh nel DB."""
    access = crea_token({"sub": str(utente.id)})
    refresh = crea_refresh_token()
    scadenza = scadenza_refresh_token()

    # Salva refresh token nel DB
    db.add(RefreshToken(
        utente_id=utente.id,
        token=refresh,
        scadenza=scadenza,
    ))
    db.commit()
    return access, refresh


# ============================================================
# REGISTRAZIONE
# ============================================================
@router.post("/registrati", response_model=TokenResponse, status_code=201)
def registrati(dati: RegistrazioneRequest, db: Session = Depends(get_db)):
    if db.query(Utente).filter(Utente.email == dati.email).first():
        raise HTTPException(status_code=400, detail="Email già registrata")

    if db.query(Utente).filter(Utente.username == dati.username).first():
        raise HTTPException(status_code=400, detail="Username già in uso")

    utente = Utente(
        nome=dati.nome,
        username=dati.username,
        email=dati.email,
        password_hash=hash_password(dati.password),
    )
    db.add(utente)
    db.flush()
    db.add(Streak(utente_id=utente.id, giorni=0))
    db.commit()
    db.refresh(utente)

    access, refresh = _crea_token_coppia(utente, db)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        utente=_utente_response(utente, db),
    )


# ============================================================
# LOGIN
# ============================================================
@router.post("/login", response_model=TokenResponse)
def login(dati: LoginRequest, db: Session = Depends(get_db)):
    utente = db.query(Utente).filter(Utente.email == dati.email).first()
    if not utente or not verifica_password(dati.password, utente.password_hash or ""):
        raise HTTPException(status_code=401, detail="Email o password errati")

    access, refresh = _crea_token_coppia(utente, db)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        utente=_utente_response(utente, db),
    )


# ============================================================
# REFRESH — rinnova l'access token senza rifare il login
# ============================================================
@router.post("/refresh")
def refresh(dati: dict, db: Session = Depends(get_db)):
    token_str = dati.get("refresh_token")
    if not token_str:
        raise HTTPException(status_code=400, detail="Refresh token mancante")

    # Cerca nel DB
    rt = db.query(RefreshToken).filter(
        RefreshToken.token == token_str,
        RefreshToken.revocato == False,
    ).first()

    if not rt:
        raise HTTPException(status_code=401, detail="Refresh token non valido")

    # Controlla scadenza
    ora = datetime.now(timezone.utc)
    scadenza = rt.scadenza.replace(tzinfo=timezone.utc) \
        if rt.scadenza.tzinfo is None else rt.scadenza

    if ora > scadenza:
        rt.revocato = True
        db.commit()
        raise HTTPException(status_code=401, detail="Refresh token scaduto")

    # Revoca il vecchio refresh token (rotation)
    rt.revocato = True
    db.flush()

    # Emetti nuova coppia di token
    utente = db.query(Utente).filter(Utente.id == rt.utente_id).first()
    if not utente:
        raise HTTPException(status_code=404, detail="Utente non trovato")

    access, nuovo_refresh = _crea_token_coppia(utente, db)
    return {
        "access_token": access,
        "refresh_token": nuovo_refresh,
        "token_type": "bearer",
    }


# ============================================================
# LOGOUT — revoca il refresh token
# ============================================================
@router.post("/logout")
def logout(dati: dict, db: Session = Depends(get_db)):
    token_str = dati.get("refresh_token")
    if token_str:
        rt = db.query(RefreshToken).filter(
            RefreshToken.token == token_str
        ).first()
        if rt:
            rt.revocato = True
            db.commit()
    return {"messaggio": "Logout effettuato"}


# ============================================================
# PROFILO CORRENTE
# ============================================================
@router.get("/me", response_model=UtenteResponse)
def get_me(
    utente: Utente = Depends(get_utente_corrente),
    db: Session = Depends(get_db),
):
    return _utente_response(utente, db)


# ============================================================
# HELPER — COUNT queries efficienti (no N+1)
# ============================================================
def _utente_response(u: Utente, db: Session) -> UtenteResponse:
    num_follower = db.query(func.count(Follow.id)).filter(
        Follow.seguito_id == u.id
    ).scalar() or 0

    num_seguiti = db.query(func.count(Follow.id)).filter(
        Follow.follower_id == u.id
    ).scalar() or 0

    num_post = db.query(func.count(Post.id)).filter(
        Post.autore_id == u.id
    ).scalar() or 0

    return UtenteResponse(
        id=u.id,
        nome=u.nome,
        username=u.username,
        email=u.email,
        bio=u.bio or "",
        foto_profilo=u.foto_profilo,
        is_privato=u.is_privato,
        num_follower=num_follower,
        num_seguiti=num_seguiti,
        num_post=num_post,
        streak_giorni=u.streak.giorni if u.streak else 0,
        onboarding_completato=u.onboarding_completato,
        creato_at=u.creato_at,
    )