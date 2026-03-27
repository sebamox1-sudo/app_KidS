from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from app.database import get_db
from app.models.modelli import Utente, Streak, Follow, Post
from app.schemas.schemi import RegistrazioneRequest, LoginRequest, TokenResponse, UtenteResponse
from app.services.auth_service import hash_password, verifica_password, crea_token
from app.dependencies import get_utente_corrente

from fastapi import APIRouter, Depends, HTTPException, status, Request # <--- Aggiungi Request
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)  # limitatore per questo modulo
router = APIRouter(prefix="/auth", tags=["Auth"])

# ============================================================
# REGISTRAZIONE
# ============================================================
@router.post("/registrati", response_model=TokenResponse, status_code=201)
@limiter.limit("3/hour")
def registrati(
    request: Request,
    dati: RegistrazioneRequest, 
    db: Session = Depends(get_db)
    ):
    # Controlla email duplicata
    if db.query(Utente).filter(Utente.email == dati.email).first():
        raise HTTPException(
            status_code=400,
            detail="Email già registrata"
        )

    # Controlla username duplicato
    if db.query(Utente).filter(Utente.username == dati.username).first():
        raise HTTPException(
            status_code=400,
            detail="Username già in uso"
        )

    # Crea utente
    utente = Utente(
        nome=dati.nome,
        username=dati.username,
        email=dati.email,
        password_hash=hash_password(dati.password),
    )
    db.add(utente)
    db.flush()

    # Crea streak iniziale
    streak = Streak(utente_id=utente.id, giorni=0)
    db.add(streak)
    db.commit()
    db.refresh(utente)

    # Genera token
    token = crea_token({"sub": str(utente.id)})
    return TokenResponse(
        access_token=token,
        utente=_utente_response(utente, db)
    )


# ============================================================
# LOGIN
# ============================================================
@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
def login(
    request: Request,
    dati: LoginRequest, 
    db: Session = Depends(get_db)
    ):
    utente = db.query(Utente).filter(Utente.email == dati.email).first()

    if not utente or not verifica_password(dati.password, utente.password_hash or ""):
        raise HTTPException(
            status_code=401,
            detail="Email o password errati"
        )

    token = crea_token({"sub": str(utente.id)})
    return TokenResponse(
        access_token=token,
        utente=_utente_response(utente, db)
    )


# ============================================================
# PROFILO CORRENTE
# ============================================================
@router.get("/me", response_model=UtenteResponse)
def get_me(utente: Utente = Depends(get_utente_corrente),
           db: Session = Depends(get_db)):
    return _utente_response(utente, db)


# ============================================================
# HELPER — usa COUNT queries efficienti (no N+1)
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

    # 🔥 CALCOLO DELLA POSIZIONE IN CLASSIFICA BASATO SULLO STREAK
    mio_streak = u.streak.giorni if u.streak else 0
    # Contiamo quanti utenti hanno uno streak MAGGIORE di questo utente
    utenti_davanti = db.query(Utente).outerjoin(Streak).filter(
        # Ha più punti di me
        (case((Streak.giorni != None, Streak.giorni), else_=0) > mio_streak) |
        # OPPURE ha gli stessi punti ma si è iscritto prima di me
        ((case((Streak.giorni != None, Streak.giorni), else_=0) == mio_streak) & (Utente.id < u.id))
    ).count()
    # Se ha 0 di streak, potremmo volerlo escludere dal podio assegnandogli una posizione alta finta o 0.
    # Ma per logica standard, la sua posizione è: (quelli davanti) + 1
    posizione_attuale = utenti_davanti + 1 if mio_streak > 0 else 0
    # ATTENZIONE: Se UtenteResponse (in schemi.py) usa pydantic, 
    # assicurati di aggiungere 'posizione_classifica: int = 0' al modello UtenteResponse in schemi.py!

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
        sfide_partecipate= u.sfide_partecipate,
        sfide_vinte= u.sfide_vinte,
        voti_dati= u.voti_dati,
        commenti_scritti= u.commenti_scritti,
        like_ricevuti= u.like_ricevuti,
        posizione_classifica=posizione_attuale
    )