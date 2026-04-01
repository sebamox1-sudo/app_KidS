from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models.modelli import Notifica, Utente
from app.schemas.schemi import NotificaResponse
from app.dependencies import get_utente_corrente
from app.routers.auth import _utente_response

router = APIRouter(prefix="/notifiche", tags=["Notifiche"])


@router.get("/", response_model=List[NotificaResponse])
def get_notifiche(
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    notifiche = db.query(Notifica).filter(
        Notifica.destinatario_id == me.id
    ).order_by(Notifica.creato_at.desc()).limit(50).all()
    return [_notifica_response(n, db) for n in notifiche]


@router.patch("/{notifica_id}/leggi")
def segna_letta(
    notifica_id: int,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    n = db.query(Notifica).filter(
        Notifica.id == notifica_id,
        Notifica.destinatario_id == me.id
    ).first()
    if n:
        n.letta = True
        db.commit()
    return {"messaggio": "ok"}


@router.patch("/leggi-tutte")
def segna_tutte_lette(
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    db.query(Notifica).filter(
        Notifica.destinatario_id == me.id,
        Notifica.letta == False
    ).update({"letta": True})
    db.commit()
    return {"messaggio": "Tutte le notifiche segnate come lette"}


@router.delete("/svuota/tutte")
def cancella_tutte_notifiche(db: Session = Depends(get_db), me: Utente = Depends(get_utente_corrente)):
    try:
        elementi_cancellati = db.query(Notifica).filter(Notifica.destinatario_id == me.id).delete(synchronize_session=False)
        db.commit()
        return {"successo": True, "messaggio": f"Eliminate {elementi_cancellati} notifiche."}
    except Exception as e:
        db.rollback()
        return {"successo": False, "errore": str(e)}


@router.delete("/{notifica_id}")
def elimina_notifica(
    notifica_id: int,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    n = db.query(Notifica).filter(
        Notifica.id == notifica_id,
        Notifica.destinatario_id == me.id
    ).first()
    if n:
        db.delete(n)
        db.commit()
    return {"messaggio": "Notifica eliminata"}


@router.get("/non-lette/count")
def count_non_lette(
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    count = db.query(Notifica).filter(
        Notifica.destinatario_id == me.id,
        Notifica.letta == False
    ).count()
    return {"count": count}


def _notifica_response(n: Notifica, db: Session) -> NotificaResponse:
    richiesta_id = None
    stato_richiesta = None # ✨ 1. CREIAMO LA VARIABILE PER LO STATO
    if n.tipo == 'richiesta_follow' and n.mittente_id:
        from app.models.modelli import RichiestaFollow
        # ✨ 2. CERCHIAMO LA RICHIESTA SENZA FILTRARE LO STATO
        r = db.query(RichiestaFollow).filter(
            RichiestaFollow.richiedente_id == n.mittente_id,
            RichiestaFollow.destinatario_id == n.destinatario_id
        ).first()
        
        if r:
            richiesta_id = r.id
            stato_richiesta = r.stato # ✨ 3. PRENDIAMO LO STATO ("in_attesa", "accettata", "rifiutata")

    return NotificaResponse(
        id=n.id,
        tipo=n.tipo,
        testo=n.testo,
        letta=n.letta,
        mittente=_utente_response(n.mittente, db) if n.mittente else None,
        richiesta_id=richiesta_id,
        stato_richiesta=stato_richiesta, # ✨ 4. LO SPEDIAMO A FLUTTER!
        creato_at=n.creato_at,
    )
