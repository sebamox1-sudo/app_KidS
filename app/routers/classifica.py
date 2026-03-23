from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from typing import List
from app.database import get_db
from app.models.modelli import Utente, Streak
from app.dependencies import get_utente_corrente
from app.routers.auth import _utente_response

router = APIRouter(prefix="/classifica", tags=["Classifica"])


@router.get("/")
def get_classifica(
    limit: int = 50,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    utenti = db.query(Utente).outerjoin(Streak).order_by(
        case((Streak.giorni.isnot(None), Streak.giorni), else_=0).desc()
    ).limit(limit).all()

    return [{
        "posizione": i + 1,
        "utente": _utente_response(u, db),
        "streak": u.streak.giorni if u.streak else 0,
        "sono_io": u.id == me.id,
    } for i, u in enumerate(utenti)]


@router.get("/mia-posizione")
def get_mia_posizione(
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    mio_streak = me.streak.giorni if me.streak else 0
    utenti_davanti = db.query(Utente).outerjoin(Streak).filter(
        case((Streak.giorni != None, Streak.giorni), else_=0) > mio_streak
    ).count()
    return {
        "posizione": utenti_davanti + 1,
        "streak": mio_streak,
    }