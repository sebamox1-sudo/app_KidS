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


# ============================================================
# LISTA SONDAGGI — solo quelli non scaduti, degli amici
# ============================================================
@router.get("/", response_model=List[SondaggioResponse])
def get_sondaggi(
    skip: int = 0, limit: int = 20,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    ora = datetime.now(timezone.utc)
    seguiti_ids = [f.seguito_id for f in me.seguiti_rel]

    # Mostra solo sondaggi non scaduti degli amici o propri
    sondaggi = db.query(Sondaggio).filter(
        (Sondaggio.autore_id.in_(seguiti_ids)) | (Sondaggio.autore_id == me.id),
        Sondaggio.scadenza > ora
    ).order_by(
        Sondaggio.creato_at.desc()
    ).offset(skip).limit(limit).all()

    if not sondaggi:
        return []

    # Batch: prendi tutti i voti in una sola query
    sondaggi_ids = [s.id for s in sondaggi]
    tutti_i_voti = db.query(VotoSondaggio).filter(
        VotoSondaggio.sondaggio_id.in_(sondaggi_ids)
    ).all()

    voti_per_sondaggio = defaultdict(list)
    for v in tutti_i_voti:
        voti_per_sondaggio[v.sondaggio_id].append(v)

    return [
        _sondaggio_response_batch(s, voti_per_sondaggio.get(s.id, []), me.id, db)
        for s in sondaggi
    ]


# ============================================================
# CREA SONDAGGIO — con durata variabile
# ============================================================
@router.post("/", response_model=SondaggioResponse, status_code=201)
def crea_sondaggio(
    dati: SondaggioRequest,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    durata_ore = dati.durata_ore if dati.durata_ore else 24
    scadenza = datetime.now(timezone.utc) + timedelta(hours=durata_ore)

    sondaggio = Sondaggio(
        autore_id=me.id,
        domanda=dati.domanda,
        opzioni=json.dumps(dati.opzioni),
        scadenza=scadenza,
    )
    db.add(sondaggio)

    # Notifica follower
    for follow in me.follower_rel:
        db.add(Notifica(
            destinatario_id=follow.follower_id,
            mittente_id=me.id,
            tipo="sondaggio",
            testo=f'{me.nome} ha creato un sondaggio: "{dati.domanda[:40]}"',
        ))

    db.commit()
    db.refresh(sondaggio)

    # Ritorna il sondaggio creato (nessun voto ancora)
    return _sondaggio_response_batch(sondaggio, [], me.id, db)


# ============================================================
# VOTA SONDAGGIO
# ============================================================
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

    # Controlla scadenza
    ora = datetime.now(timezone.utc)
    scadenza = sondaggio.scadenza
    if scadenza.tzinfo is None:
        scadenza = scadenza.replace(tzinfo=timezone.utc)
    if ora > scadenza:
        raise HTTPException(status_code=400, detail="Sondaggio scaduto")

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

    if sondaggio.autore_id != me.id:
        testo = (
            "Un utente anonimo ha votato nel tuo sondaggio"
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

@router.get("/{sondaggio_id}/voti")
def get_voti_sondaggio(
    sondaggio_id: int, 
    db: Session = Depends(get_db), 
    me: Utente = Depends(get_utente_corrente)
    ):
    # 1. Trova il sondaggio
    sondaggio = db.query(Sondaggio).filter(Sondaggio.id == sondaggio_id).first()
    
    if not sondaggio:
        return {"successo": False, "errore": "Sondaggio non trovato"}
        
    # 2. Controllo di sicurezza: SOLO L'AUTORE PUÒ VEDERE I VOTI!
    if sondaggio.autore_id != me.id:
        return {"successo": False, "errore": "Non sei l'autore di questo sondaggio"}
        
    # 3. Recupera i voti
    voti_db = db.query(VotoSondaggio).filter(VotoSondaggio.sondaggio_id == sondaggio_id).all()
    
    risultato = []
    for v in voti_db:
        utente = v.utente # Assumendo la relationship
        if v.is_anonimo:
            # Nascondiamo l'identità!
            risultato.append({
                "nome": "Voto Anonimo",
                "avatar": None,
                "is_anonimo": True,
                "opzione_index": v.opzione_index
            })
        else:
            # Mostriamo l'identità
            risultato.append({
                "nome": utente.nome,
                "username": utente.username,
                "avatar": utente.foto_profilo,
                "is_anonimo": False,
                "opzione_index": v.opzione_index
            })
            
    return {"successo": True, "dati": risultato}


# ============================================================
# HELPER
# ============================================================
def _sondaggio_response_batch(
    s: Sondaggio,
    voti_del_sondaggio: list,
    utente_id: int,
    db: Session,
) -> SondaggioResponse:
    opzioni = json.loads(s.opzioni)

    voti_per_opzione = [
        len([v for v in voti_del_sondaggio if v.opzione_index == i])
        for i in range(len(opzioni))
    ]

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
        scadenza=s.scadenza,
        creato_at=s.creato_at,
    )