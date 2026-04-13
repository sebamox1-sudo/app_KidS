from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone
from pydantic import BaseModel, field_validator
from app.database import get_db
from app.models.modelli import Utente, Streak, Follow, Post, RefreshToken
from app.schemas.schemi import (
    RegistrazioneRequest, LoginRequest, TokenResponse,
    UtenteResponse, UtentePublicResponse,
)
from app.services.auth_service import (
    hash_password, verifica_password, crea_token,
    crea_refresh_token, scadenza_refresh_token,
)
from app.dependencies import get_utente_corrente
from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request

router = APIRouter(prefix="/auth", tags=["Auth"])

limiter = Limiter(key_func=get_remote_address)


# ============================================================
# REQUEST MODELS TIPATI (fix C-3 / H-4)
# ============================================================
class LogoutRequest(BaseModel):
    """Tipato — Pydantic valida prima che il codice venga eseguito."""
    refresh_token: str

    @field_validator("refresh_token")
    @classmethod
    def token_formato_valido(cls, v):
        # secrets.token_hex(64) produce sempre esattamente 128 caratteri hex.
        # Un token con lunghezza diversa è sicuramente malformato.
        if len(v) != 128:
            raise ValueError("Token non valido")
        return v


def _crea_token_coppia(utente: Utente, db: Session) -> tuple[str, str]:
    """Crea access token + refresh token e salva il refresh nel DB."""
    access = crea_token({"sub": str(utente.id)})
    refresh = crea_refresh_token()
    scadenza = scadenza_refresh_token()

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
@limiter.limit("3/minute")
def registrati(request: Request, dati: RegistrazioneRequest, db: Session = Depends(get_db)):
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
@limiter.limit("5/minute")
def login(request: Request, dati: LoginRequest, db: Session = Depends(get_db)):
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
@limiter.limit("10/minute")
def refresh(request: Request, dati: dict, db: Session = Depends(get_db)):
    token_str = dati.get("refresh_token")
    if not token_str:
        raise HTTPException(status_code=400, detail="Refresh token mancante")

    rt = db.query(RefreshToken).filter(
        RefreshToken.token == token_str,
        RefreshToken.revocato == False,
    ).first()

    if not rt:
        raise HTTPException(status_code=401, detail="Refresh token non valido")

    ora = datetime.now(timezone.utc)
    scadenza = rt.scadenza.replace(tzinfo=timezone.utc) \
        if rt.scadenza.tzinfo is None else rt.scadenza

    if ora > scadenza:
        rt.revocato = True
        db.commit()
        raise HTTPException(status_code=401, detail="Refresh token scaduto")

    rt.revocato = True
    db.flush()

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
# LOGOUT — revoca il refresh token (fix H-4)
# ============================================================
@router.post("/logout")
@limiter.limit("10/minute")
def logout(
    request: Request,
    dati: LogoutRequest,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente),  # FIX H-4: auth obbligatoria
):
    """
    Revoca il refresh token dell'utente autenticato.
    SICUREZZA: verifica che il token appartenga a ME (me.id) —
    impedisce di revocare sessioni di altri utenti.
    """
    rt = db.query(RefreshToken).filter(
        RefreshToken.token == dati.refresh_token,
        RefreshToken.utente_id == me.id,   # ← solo il proprietario
    ).first()
    if rt:
        rt.revocato = True
        db.commit()
    # Risposta sempre positiva: non rivela se il token esisteva o no.
    return {"messaggio": "Logout effettuato"}


# ============================================================
# PROFILO CORRENTE — schema PRIVATO con email
# ============================================================
@router.get("/me", response_model=UtenteResponse)
def get_me(
    utente: Utente = Depends(get_utente_corrente),
    db: Session = Depends(get_db),
):
    return _utente_response(utente, db)


# ============================================================
# HELPER — _utente_response (PRIVATO, con email) — solo per /me
# ============================================================
def _utente_response(u: Utente, db: Session) -> UtenteResponse:
    """
    Restituisce lo schema PRIVATO (con email).
    Usare SOLO per endpoint dell'utente su sé stesso:
    /auth/me, /auth/login, /auth/registrati, /utenti/me/profilo, /utenti/me/foto
    """
    num_follower = db.query(func.count(Follow.id)).filter(
        Follow.seguito_id == u.id
    ).scalar() or 0

    num_seguiti = db.query(func.count(Follow.id)).filter(
        Follow.follower_id == u.id
    ).scalar() or 0

    num_post = db.query(func.count(Post.id)).filter(
        Post.autore_id == u.id,
        Post.foto_principale.isnot(None),
    ).scalar() or 0

    seguiti_ids = {f.seguito_id for f in u.seguiti_rel}
    follower_ids = {f.follower_id for f in u.follower_rel}
    num_amici = len(seguiti_ids & follower_ids)

    badge_sbloccati = [b.tipo for b in u.badge]

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
        streak_ultimo_post=u.streak.ultimo_post if u.streak else None,
        onboarding_completato=u.onboarding_completato,
        creato_at=u.creato_at,
        num_amici=num_amici,
        badge_sbloccati=badge_sbloccati,
    )


# ============================================================
# HELPER — _utente_public_response (PUBBLICO, senza email)
# ============================================================
def _utente_public_response(u: Utente, db: Session) -> UtentePublicResponse:
    """
    Restituisce lo schema PUBBLICO (senza email, senza dati interni).
    Usare per: GET /utenti/{username}, autore nei post/commenti/sfide/sondaggi,
               mittente nelle notifiche, liste follower/seguiti/amici altrui.
    """
    num_follower = db.query(func.count(Follow.id)).filter(
        Follow.seguito_id == u.id
    ).scalar() or 0

    num_seguiti = db.query(func.count(Follow.id)).filter(
        Follow.follower_id == u.id
    ).scalar() or 0

    num_post = db.query(func.count(Post.id)).filter(
        Post.autore_id == u.id,
        Post.foto_principale.isnot(None),
    ).scalar() or 0

    seguiti_ids = {f.seguito_id for f in u.seguiti_rel}
    follower_ids = {f.follower_id for f in u.follower_rel}
    num_amici = len(seguiti_ids & follower_ids)

    badge_sbloccati = [b.tipo for b in u.badge]

    return UtentePublicResponse(
        id=u.id,
        nome=u.nome,
        username=u.username,
        bio=u.bio or "",
        foto_profilo=u.foto_profilo,
        is_privato=u.is_privato,
        num_follower=num_follower,
        num_seguiti=num_seguiti,
        num_post=num_post,
        streak_giorni=u.streak.giorni if u.streak else 0,
        streak_ultimo_post=u.streak.ultimo_post if u.streak else None,
        num_amici=num_amici,
        badge_sbloccati=badge_sbloccati,
    )