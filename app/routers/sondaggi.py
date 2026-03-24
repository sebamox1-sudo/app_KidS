import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models.modelli import Sondaggio, VotoSondaggio, Notifica, Utente
from app.schemas.schemi import SondaggioRequest, SondaggioResponse, VotoSondaggioRequest
from app.dependencies import get_utente_corrente
from app.routers.auth import _utente_response
from collections import defaultdict
from datetime import datetime, timedelta, timezone

router = APIRouter(prefix="/sondaggi", tags=["Sondaggi"])


@router.get("/", response_model=List[SondaggioResponse])
def get_sondaggi(
    skip: int = 0, limit: int = 20,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    # Calcoliamo esattamente la data e l'ora di 24 ore fa
    limite_tempo = datetime.now(timezone.utc) - timedelta(hours=24)

    # Prendi gli ID delle persone che l'utente segue
    seguiti_ids = [f.seguito_id for f in me.seguiti_rel]

    # Filtra i sondaggi: mostra solo quelli degli amici (seguiti) o i propri
    sondaggi = db.query(Sondaggio).filter(
        (Sondaggio.autore_id.in_(seguiti_ids)) | (Sondaggio.autore_id == me.id),
        Sondaggio.creato_at >= limite_tempo
    ).order_by(
        Sondaggio.creato_at.desc()
    ).offset(skip).limit(limit).all()

    if not sondaggi:
        return []
    
    # ── BATCH: Prendi tutti i voti dei sondaggi in UNA sola query ──
    sondaggi_ids = [s.id for s in sondaggi]
    tutti_i_voti = db.query(VotoSondaggio).filter(
        VotoSondaggio.sondaggio_id.in_(sondaggi_ids)
    ).all()
    # Raggruppa i voti per sondaggio_id in un dizionario in memoria
    voti_per_sondaggio = defaultdict(list)
    for v in tutti_i_voti:
        voti_per_sondaggio[v.sondaggio_id].append(v)
    # Usiamo la nuova funzione ottimizzata passando i voti pre-caricati

    return [
        _sondaggio_response_batch(s, voti_per_sondaggio.get(s.id, []), me.id, db)
        for s in sondaggi
    ]


@router.post("/", response_model=SondaggioResponse, status_code=201)
def crea_sondaggio(
    dati: SondaggioRequest,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    # Calcolo della scadenza basato sulla scelta dell'utente
    durata = dati.durata_ore if dati.durata_ore else 24
    scadenza_calcolata = datetime.now(timezone.utc) + timedelta(hours=dati.durata_ore)
    sondaggio = Sondaggio(
        autore_id=me.id,
        domanda=dati.domanda,
        opzioni=json.dumps(dati.opzioni),
        scadenza=scadenza_calcolata # Salviamo la data esatta di fine
    )
    db.add(sondaggio)
    db.commit()
    db.refresh(sondaggio)
    return SondaggioResponse(sondaggio, me.id, db)


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
        utente_id=me.id,
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


def _sondaggio_response_batch(s: Sondaggio, voti_del_sondaggio: list, utente_id: int, db: Session) -> SondaggioResponse:
    """Risposta per i sondaggi in lista — usa dati pre-caricati in batch (zero query extra)."""
    opzioni = json.loads(s.opzioni)
    
    # Calcola i voti scorrendo la lista in memoria (niente lazy loading dal DB)
    voti_per_opzione = [
        len([v for v in voti_del_sondaggio if v.opzione_index == i])
        for i in range(len(opzioni))
    ]
    
    # Cerca il voto dell'utente corrente nella lista in memoria
    mio_voto = next(
        (v for v in voti_del_sondaggio if v.utente_id == utente_id), None
    )

    return SondaggioResponse(
        id=s.id,
        autore=_utente_response(s.autore, db),
        domanda=s.domanda,
        opzioni=opzioni,
        voti_per_opzione=voti_per_opzione,
        totale_voti=len(voti_del_sondaggio),
        ho_votato=mio_voto is not None,
        mia_opzione=mio_voto.opzione_index if mio_voto else None,
        creato_at=s.creato_at,
    )