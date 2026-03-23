import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models.modelli import Sondaggio, VotoSondaggio, Notifica, Utente
from app.schemas.schemi import SondaggioRequest, SondaggioResponse, VotoSondaggioRequest
from app.dependencies import get_utente_corrente
from app.routers.auth import _utente_response

router = APIRouter(prefix="/sondaggi", tags=["Sondaggi"])


@router.get("/", response_model=List[SondaggioResponse])
def get_sondaggi(
    skip: int = 0, limit: int = 20,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    sondaggi = db.query(Sondaggio).order_by(
        Sondaggio.creato_at.desc()
    ).offset(skip).limit(limit).all()
    return [_sondaggio_response(s, me.id, db) for s in sondaggi]


@router.post("/", response_model=SondaggioResponse, status_code=201)
def crea_sondaggio(
    dati: SondaggioRequest,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    sondaggio = Sondaggio(
        autore_id=me.id,
        domanda=dati.domanda,
        opzioni=json.dumps(dati.opzioni),
    )
    db.add(sondaggio)
    db.commit()
    db.refresh(sondaggio)
    return _sondaggio_response(sondaggio, me.id, db)


@router.post("/{sondaggio_id}/vota")
def vota_sondaggio(
    sondaggio_id: int,
    dati: VotoSondaggioRequest,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    sondaggio = db.query(Sondaggio).filter(
        Sondaggio.id == sondaggio_id).first()
    if not sondaggio:
        raise HTTPException(status_code=404, detail="Sondaggio non trovato")

    esiste = db.query(VotoSondaggio).filter(
        VotoSondaggio.sondaggio_id == sondaggio_id,
        VotoSondaggio.utente_id == me.id
    ).first()
    if esiste:
        raise HTTPException(status_code=400, detail="Hai già votato")

    opzioni = json.loads(sondaggio.opzioni)
    if dati.opzione_index >= len(opzioni):
        raise HTTPException(status_code=400, detail="Opzione non valida")

    voto = VotoSondaggio(
        utente_id=None if dati.anonimo else me.id,
        sondaggio_id=sondaggio_id,
        opzione_index=dati.opzione_index,
        anonimo=dati.anonimo,
    )
    db.add(voto)

    # Notifica autore
    if sondaggio.autore_id != me.id:
        testo = (
            f"Un utente anonimo ha votato nel tuo sondaggio"
            if dati.anonimo
            else f"{me.nome} ha votato nel tuo sondaggio"
        )
        db.add(Notifica(
            destinatario_id=sondaggio.autore_id,
            mittente_id=None if dati.anonimo else me.id,
            tipo="sondaggio",
            testo=testo,
        ))

    db.commit()
    return {"messaggio": "Voto registrato"}


def _sondaggio_response(s: Sondaggio, utente_id: int, db: Session) -> SondaggioResponse:
    opzioni = json.loads(s.opzioni)
    voti_per_opzione = [
        len([v for v in s.voti if v.opzione_index == i])
        for i in range(len(opzioni))
    ]
    mio_voto = next(
        (v for v in s.voti if v.utente_id == utente_id), None)

    return SondaggioResponse(
        id=s.id,
        autore=_utente_response(s.autore, db),
        domanda=s.domanda,
        opzioni=opzioni,
        voti_per_opzione=voti_per_opzione,
        totale_voti=len(s.voti),
        ho_votato=mio_voto is not None,
        mia_opzione=mio_voto.opzione_index if mio_voto else None,
        creato_at=s.creato_at,
    )