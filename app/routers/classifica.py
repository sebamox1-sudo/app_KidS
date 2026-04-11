from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, case, or_, and_
from typing import List
from app.database import get_db
from app.models.modelli import Utente, Streak
from app.dependencies import get_utente_corrente

router = APIRouter(prefix="/classifica", tags=["Classifica"])


def _utente_classifica(u: Utente) -> dict:
    return {
        "id": u.id,
        "nome": u.nome,
        "username": u.username,
        "foto_profilo": u.foto_profilo,
    }


@router.get("/")
def get_classifica(
    limit: int = 50,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    utenti = db.query(Utente).outerjoin(Streak).order_by(
        case((Streak.giorni.isnot(None), Streak.giorni), else_=0).desc(),
        Utente.id.asc()
    ).limit(limit).all()

    return [{
        "posizione": i + 1,
        "utente": _utente_classifica(u),
        "streak": u.streak.giorni if u.streak else 0,
        "sono_io": u.id == me.id,
    } for i, u in enumerate(utenti)]


@router.get("/amici")
def get_classifica_amici(
    limit: int = 50,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente),
):
    seguiti_ids = {f.seguito_id for f in me.seguiti_rel}
    follower_ids = {f.follower_id for f in me.follower_rel}
    amici_ids = (seguiti_ids & follower_ids) | {me.id}

    utenti = (
        db.query(Utente)
        .outerjoin(Streak)
        .filter(Utente.id.in_(amici_ids))
        .order_by(
            case((Streak.giorni != None, Streak.giorni), else_=0).desc(),
            Utente.id.asc()
        )
        .limit(limit)
        .all()
    )

    return [{
        "posizione": i + 1,
        "utente": _utente_classifica(u),
        "streak": u.streak.giorni if u.streak else 0,
        "sono_io": u.id == me.id,
    } for i, u in enumerate(utenti)]


@router.get("/mia-posizione")
def get_mia_posizione(
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    mio_streak = me.streak.giorni if me.streak else 0
    streak_expr = case((Streak.giorni.isnot(None), Streak.giorni), else_=0)

    utenti_davanti = db.query(func.count(Utente.id)).outerjoin(Streak).filter(
        or_(
            streak_expr > mio_streak,
            and_(streak_expr == mio_streak, Utente.id < me.id)
        )
    ).scalar()

    return {
        "posizione": utenti_davanti + 1,
        "streak": mio_streak,
    }