from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime, timezone, timedelta
from app.database import get_db
from app.models.modelli import Sfida, PartecipazioneSfida, VotoSfida, Notifica, Utente
from app.schemas.schemi import SfidaRequest, SfidaResponse
from app.dependencies import get_utente_corrente
from app.routers.auth import _utente_response
import aiofiles, os, uuid

router = APIRouter(prefix="/sfide", tags=["Sfide"])
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")


@router.get("/attiva", response_model=Optional[SfidaResponse])
def get_sfida_attiva(
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    ora = datetime.now(timezone.utc)
    sfida = db.query(Sfida).filter(
        Sfida.scadenza > ora
    ).order_by(Sfida.creato_at.desc()).first()

    if not sfida:
        return None
    return _sfida_response(sfida, me.id, db)


@router.post("/", response_model=SfidaResponse, status_code=201)
def crea_sfida(
    dati: SfidaRequest,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    ora = datetime.now(timezone.utc)
    sfida = Sfida(
        autore_id=me.id,
        tema=dati.tema,
        durata_ore=dati.durata_ore,
        scadenza=ora + timedelta(hours=dati.durata_ore),
    )
    db.add(sfida)

    # Notifica a tutti i follower
    for follow in me.follower_rel:
        db.add(Notifica(
            destinatario_id=follow.follower_id,
            mittente_id=me.id,
            tipo="sfida",
            testo=f"{me.nome} ha lanciato una sfida: {dati.tema} ⚡",
        ))

    db.commit()
    db.refresh(sfida)
    return _sfida_response(sfida, me.id, db)


@router.post("/{sfida_id}/partecipa", status_code=201)
async def partecipa_sfida(
    sfida_id: int,
    foto: UploadFile = File(...),
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    sfida = db.query(Sfida).filter(Sfida.id == sfida_id).first()
    if not sfida:
        raise HTTPException(status_code=404, detail="Sfida non trovata")
    if sfida.is_scaduta:
        raise HTTPException(status_code=400, detail="Sfida scaduta")

    # Salva foto
    os.makedirs(f"{UPLOAD_DIR}/sfide", exist_ok=True)
    ext = foto.filename.split(".")[-1]
    nome = f"{uuid.uuid4()}.{ext}"
    percorso = f"{UPLOAD_DIR}/sfide/{nome}"
    async with aiofiles.open(percorso, "wb") as f:
        await f.write(await foto.read())

    partecipazione = PartecipazioneSfida(
        sfida_id=sfida_id,
        utente_id=me.id,
        foto_url=f"/uploads/sfide/{nome}",
    )
    db.add(partecipazione)
    db.commit()
    return {"messaggio": "Partecipazione registrata"}


@router.get("/{sfida_id}/partecipazioni")
def get_partecipazioni(
    sfida_id: int,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    sfida = db.query(Sfida).filter(Sfida.id == sfida_id).first()
    if not sfida:
        raise HTTPException(status_code=404, detail="Sfida non trovata")

    ho_partecipato = any(
        p.utente_id == me.id for p in sfida.partecipazioni)
    if not ho_partecipato and sfida.autore_id != me.id:
        raise HTTPException(
            status_code=403,
            detail="Devi partecipare per vedere le foto")

    return [{
        "id": p.id,
        "utente": _utente_response(p.utente, db),
        "foto_url": p.foto_url,
        "media_voti": p.media_voti,
        "ho_votato": any(v.votante_id == me.id for v in p.voti),
    } for p in sfida.partecipazioni]


@router.post("/partecipazioni/{partecipazione_id}/vota")
def vota_partecipazione(
    partecipazione_id: int,
    voto: float,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    p = db.query(PartecipazioneSfida).filter(
        PartecipazioneSfida.id == partecipazione_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Non trovata")

    esiste = db.query(VotoSfida).filter(
        VotoSfida.partecipazione_id == partecipazione_id,
        VotoSfida.votante_id == me.id
    ).first()
    if esiste:
        raise HTTPException(status_code=400, detail="Hai già votato")

    db.add(VotoSfida(
        partecipazione_id=partecipazione_id,
        votante_id=me.id,
        voto=voto,
    ))
    db.commit()
    return {"media_voti": p.media_voti}


def _sfida_response(s: Sfida, utente_id: int, db: Session) -> SfidaResponse:
    return SfidaResponse(
        id=s.id,
        autore=_utente_response(s.autore, db),
        tema=s.tema,
        durata_ore=s.durata_ore,
        scadenza=s.scadenza,
        is_scaduta=s.is_scaduta,
        vincitore=_utente_response(s.vincitore, db) if s.vincitore else None,
        num_partecipanti=len(s.partecipazioni),
        ho_partecipato=any(
            p.utente_id == utente_id for p in s.partecipazioni),
        creato_at=s.creato_at,
    )