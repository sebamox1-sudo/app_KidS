from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel
from app.database import get_db
from app.models.modelli import (
    Sfida, PartecipazioneSfida, VotoSfida, InvitoSfida,
    Notifica, Utente, Follow,
)
from app.schemas.schemi import SfidaRequest, SfidaResponse
from app.dependencies import get_utente_corrente
from app.routers.auth import _utente_response
import aiofiles, os, uuid

router = APIRouter(prefix="/sfide", tags=["Sfide"])
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")

class RichiestaVoto(BaseModel):
    voto: float


# ============================================================
# FEED SFIDE — mostra TUTTE le sfide attive tue e dei tuoi amici
# ============================================================
@router.get("/feed")
def get_sfide_feed(
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    ora = datetime.now(timezone.utc)
    # 1. Trova gli ID delle persone che seguo
    seguiti_ids = [f.seguito_id for f in me.seguiti_rel]
    # 2. Aggiungi il mio ID (voglio vedere le mie sfide nel feed)
    autori_validi = seguiti_ids + [me.id]
    # 3. Prendi tutte le sfide attive create da questi autori

    sfide = db.query(Sfida).filter(
        Sfida.autore_id.in_(autori_validi),
        Sfida.scadenza > ora
    ).order_by(Sfida.creato_at.desc()).all()
    # 4. Filtra ulteriormente per visibilità (se è privata, controlla se sono invitato)
    sfide_visibili = []
    for sfida in sfide:
        if _utente_puo_vedere(sfida, me, db):
            sfide_visibili.append(_sfida_response(sfida, me.id, db))

    return sfide_visibili


# ============================================================
# LE MIE SFIDE — create da me o dove sono invitato
# ============================================================
@router.get("/mie")
def get_mie_sfide(
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    ora = datetime.now(timezone.utc)

    mie = db.query(Sfida).filter(
        Sfida.autore_id == me.id,
        Sfida.scadenza > ora,
    ).all()

    inviti_ids = [i.sfida_id for i in db.query(InvitoSfida.sfida_id).filter(
        InvitoSfida.invitato_id == me.id
    ).all()]
    invitate = db.query(Sfida).filter(
        Sfida.id.in_(inviti_ids),
        Sfida.scadenza > ora,
    ).all() if inviti_ids else []

    tutte = {s.id: s for s in mie + invitate}
    return [_sfida_response(s, me.id, db) for s in tutte.values()]


# ============================================================
# CREA SFIDA — con visibilità tutti/selezionati
# ============================================================
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
        visibilita=dati.visibilita,
    )
    db.add(sfida)
    db.flush()

    if dati.visibilita == "selezionati" and dati.amici_usernames:
        # ── SFIDA PRIVATA: invita solo gli amici scelti ──
        amici_invitati = db.query(Utente).filter(
            Utente.username.in_(dati.amici_usernames)
        ).all()

        for amico in amici_invitati:
            if amico.id == me.id:
                continue

            db.add(InvitoSfida(
                sfida_id=sfida.id,
                invitato_id=amico.id,
            ))

            db.add(Notifica(
                destinatario_id=amico.id,
                mittente_id=me.id,
                tipo="sfida",
                testo=f"{me.nome} ti ha sfidato: {dati.tema} ⚡",
            ))

        if not amici_invitati:
            raise HTTPException(
                status_code=400,
                detail="Nessun amico trovato con gli username forniti"
            )
    else:
        # ── SFIDA PUBBLICA: notifica tutti i follower ──
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


# ============================================================
# PARTECIPA — con controllo visibilità
# ============================================================
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
    if not _utente_puo_vedere(sfida, me, db):
        raise HTTPException(status_code=403, detail="Non sei invitato a questa sfida")

    gia = db.query(PartecipazioneSfida).filter(
        PartecipazioneSfida.sfida_id == sfida_id,
        PartecipazioneSfida.utente_id == me.id,
    ).first()
    if gia:
        raise HTTPException(status_code=400, detail="Hai già partecipato")

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

    me.sfide_partecipate += 1

    if sfida.autore_id != me.id:
        db.add(Notifica(
            destinatario_id=sfida.autore_id,
            mittente_id=me.id,
            tipo="sfida",
            testo=f"{me.nome} ha partecipato alla tua sfida! 📸",
        ))

    db.commit()
    return {"messaggio": "Partecipazione registrata"}


# ============================================================
# PARTECIPAZIONI
# ============================================================
@router.get("/{sfida_id}/partecipazioni")
def get_partecipazioni(
    sfida_id: int,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    sfida = db.query(Sfida).filter(Sfida.id == sfida_id).first()
    if not sfida:
        raise HTTPException(status_code=404, detail="Sfida non trovata")
    if not _utente_puo_vedere(sfida, me, db):
        raise HTTPException(status_code=403, detail="Non hai accesso a questa sfida")

    ho_partecipato = any(p.utente_id == me.id for p in sfida.partecipazioni)
    if not ho_partecipato and sfida.autore_id != me.id:
        raise HTTPException(status_code=403, detail="Devi partecipare per vedere le foto")

    return [{
        "id": p.id,
        "utente": _utente_response(p.utente, db),
        "foto_url": p.foto_url,
        "media_voti": p.media_voti,
        "ho_votato": any(v.votante_id == me.id for v in p.voti),
    } for p in sfida.partecipazioni]


# ============================================================
# VOTA
# ============================================================
@router.post("/partecipazioni/{partecipazione_id}/vota")
def vota_partecipazione(
    partecipazione_id: int,
    dati: RichiestaVoto, # <-- PRIMA ERA: voto: float. ORA USIAMO IL MODELLO!
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

    # Estrai il voto dal modello Pydantic
    db.add(VotoSfida(
        partecipazione_id=partecipazione_id,
        votante_id=me.id,
        voto=dati.voto, # <-- INSERISCI QUI dati.voto
    ))
    # ✨ FIX PER I BADGE DEI VOTI!
    me.voti_dati += 1
    # per tenere traccia anche dei voti negativi per il badge "Occhio Fino":
    if dati.voto < 5.0:
        me.voti_negativi += 1
    db.commit()
    return {"media_voti": p.media_voti}


# ============================================================
# HELPERS
# ============================================================

def _utente_puo_vedere(sfida: Sfida, utente: Utente, db: Session) -> bool:
    if sfida.autore_id == utente.id:
        return True
    if sfida.visibilita == "tutti":
        return True
    invito = db.query(InvitoSfida).filter(
        InvitoSfida.sfida_id == sfida.id,
        InvitoSfida.invitato_id == utente.id,
    ).first()
    return invito is not None


def _sfida_response(s: Sfida, utente_id: int, db: Session) -> dict:
    invitati = []
    if s.visibilita == "selezionati":
        invitati = [_utente_response(inv.invitato, db) for inv in s.inviti]

    sono_invitato = any(
        inv.invitato_id == utente_id for inv in s.inviti
    ) if s.visibilita == "selezionati" else False

    # Prepariamo le partecipazioni da inviare nel feed
    partecipazioni_list = []
    for p in s.partecipazioni:
        partecipazioni_list.append({
            "id": p.id,
            "utente": _utente_response(p.utente, db),
            "foto_url": p.foto_url,
            "media_voti": p.media_voti,
            "ho_votato": any(v.votante_id == utente_id for v in p.voti),
            "creato_at": p.creato_at.isoformat() if p.creato_at else None
        })

    # Usiamo un dizionario invece di SfidaResponse per poter aggiungere 
    # dinamicamente il campo partecipazioni


    return {
        "id": s.id,
        "autore": _utente_response(s.autore, db),
        "tema": s.tema,
        "durata_ore": s.durata_ore,
        "scadenza": s.scadenza,
        "is_scaduta": s.is_scaduta,
        "visibilita": s.visibilita,
        "vincitore": _utente_response(s.vincitore, db) if s.vincitore else None,
        "num_partecipanti": len(s.partecipazioni),
        "ho_partecipato": any(p.utente_id == utente_id for p.utente_id in [part.utente_id for part in s.partecipazioni]),
        "sono_invitato": sono_invitato,
        "invitati": invitati,
        "creato_at": s.creato_at,
        "partecipazioni": partecipazioni_list # <-- Aggiunto!
    }