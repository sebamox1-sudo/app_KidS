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


@router.delete("/notifiche/cancella-tutte")
def cancella_tutte_notifiche(
    db: Session = Depends(get_db), 
    me: Utente = Depends(get_utente_corrente)
):
    try:
        # 🔥 FIX: Usiamo destinatario_id invece di utente_id!
        elementi_cancellati = db.query(Notifica).filter(Notifica.destinatario_id == me.id).delete(synchronize_session=False)
        
        # Salviamo la modifica nel database
        db.commit()
        
        return {
            "successo": True, 
            "messaggio": f"Eliminate {elementi_cancellati} notifiche."
        }
    except Exception as e:
        # Se qualcosa va storto, annulliamo l'operazione per non corrompere il database
        db.rollback()
        return {"successo": False, "errore": f"Errore durante l'eliminazione: {str(e)}"}


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
    return NotificaResponse(
        id=n.id,
        tipo=n.tipo,
        testo=n.testo,
        letta=n.letta,
        mittente=_utente_response(n.mittente, db) if n.mittente else None,
        creato_at=n.creato_at,
    )

