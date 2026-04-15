from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session, joinedload
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
from app.routers.auth import _utente_response, _utente_public_response
from app.services.fcm_service import manda_notifica
from app.services.storage_service import carica_e_comprimi_foto
from app.routers.ws_sfide import broadcast_voto
import asyncio

router = APIRouter(prefix="/sfide", tags=["Sfide"])

class RichiestaVoto(BaseModel):
    voto: float


# ============================================================
# FEED SFIDE — attive + fase classifica (2h post-scadenza per partecipanti)
# ============================================================
@router.get("/feed")
def get_sfide_feed(
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente),
):
    """Sfide attive + sfide in fase classifica per chi ha partecipato."""
    ora = datetime.now(timezone.utc)
    fine_grazia = ora - timedelta(hours=2)  # finestra di grazia

    seguiti_ids = [f.seguito_id for f in me.seguiti_rel]
    autori_validi = seguiti_ids + [me.id]

    # ── IDs delle sfide a cui ho partecipato (per la fase classifica) ──
    mie_partecipazioni_ids = {
        p.sfida_id
        for p in db.query(PartecipazioneSfida.sfida_id).filter(
            PartecipazioneSfida.utente_id == me.id,
        ).all()
    }

    # ── Query unica: attive OR in finestra di grazia ──
    # Eager-load per eliminare N+1
    sfide = (
        db.query(Sfida)
        .options(
            joinedload(Sfida.partecipazioni).joinedload(PartecipazioneSfida.utente),
            joinedload(Sfida.partecipazioni).joinedload(PartecipazioneSfida.voti),
            joinedload(Sfida.inviti).joinedload(InvitoSfida.invitato),
            joinedload(Sfida.autore),
            joinedload(Sfida.vincitore),
        )
        .filter(
            Sfida.autore_id.in_(autori_validi),
            # Attive (scadenza > ora) OPPURE in finestra grazia (scadenza > ora - 2h)
            Sfida.scadenza > fine_grazia,
        )
        .order_by(Sfida.creato_at.desc())
        .all()
    )

    risultati = []
    for s in sfide:
        if not _utente_puo_vedere(s, me, db):
            continue

        is_scaduta = ora > s.scadenza.replace(tzinfo=timezone.utc)
        in_fase_classifica = is_scaduta and s.scadenza.replace(tzinfo=timezone.utc) > fine_grazia

        # Se è in fase classifica, mostra SOLO a chi ha partecipato (o all'autore)
        if in_fase_classifica:
            if s.id not in mie_partecipazioni_ids and s.autore_id != me.id:
                continue

        risultati.append(
            _sfida_response(s, me.id, db, server_now=ora)
        )

    return risultati



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

    if dati.visibilita == "selezionati" and dati.amici_invitati:
        # ── SFIDA PRIVATA: invita solo gli amici scelti ──
        amici_invitati = db.query(Utente).filter(
            Utente.username.in_(dati.amici_invitati)
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

            manda_notifica(
                db=db,
                destinatario_id=amico.id,
                titolo="⚡ Nuova sfida!",
                corpo=f"{me.nome} ti ha sfidato: {dati.tema[:40]}",
                tipo="sfida",
                extra={
                    "sfida_id": sfida.id,
                    "tema": dati.tema,
                    "mittente_id": me.id,
                    "mittente_username": me.username,
                    "mittente_nome": me.nome,
                    "mittente_foto": me.foto_profilo or "",
                },
            )

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

            manda_notifica(
                db=db,
                destinatario_id=follow.follower_id,
                titolo="⚡ Nuova sfida!",
                corpo=f"{me.nome} ha lanciato una sfida: {dati.tema[:40]}",
                tipo="sfida",
                extra={
                    "sfida_id": sfida.id,
                    "tema": dati.tema,
                    "mittente_id": me.id,
                    "mittente_username": me.username,
                    "mittente_nome": me.nome,
                },
            )

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

    # Carica foto su Supabase — persistente anche dopo restart Railway
    url_foto = await carica_e_comprimi_foto(foto, cartella="sfide")
    partecipazione = PartecipazioneSfida(
        sfida_id=sfida_id,
        utente_id=me.id,
        foto_url=url_foto,
    )

    db.add(partecipazione)

    # Calcoliamo se è una partecipazione rapida (entro 10 min dal lancio)
    tempo_trascorso = datetime.now(timezone.utc) - sfida.creato_at
    if tempo_trascorso <= timedelta(minutes=10):
        me.sfide_rapide += 1 # ⚡️ Salva il record sul database!
    
    me.sfide_partecipate += 1

    _aggiorna_sfide_consecutive(me, db)


    if sfida.autore_id != me.id:
        db.add(Notifica(
            destinatario_id=sfida.autore_id,
            mittente_id=me.id,
            tipo="sfida",
            testo=f"{me.nome} ha partecipato alla tua sfida! 📸",
        ))


    db.commit()

    if sfida.autore_id != me.id:
        manda_notifica(
            db=db,
            destinatario_id=sfida.autore_id,
            titolo="Nuova partecipazione! 📸",
            corpo=f"{me.nome} ha partecipato alla tua sfida",
            tipo="partecipazione_sfida",
            extra={
                "sfida_id": sfida.id,
                "tema": sfida.tema,
                "mittente_id": me.id,
                "mittente_username": me.username,
                "mittente_nome": me.nome,
                "mittente_foto": me.foto_profilo or "",
            },
        )
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
    sfida = db.query(Sfida).options(
    joinedload(Sfida.partecipazioni)
    .joinedload(PartecipazioneSfida.utente),
    joinedload(Sfida.partecipazioni)
    .joinedload(PartecipazioneSfida.voti),
).filter(Sfida.id == sfida_id).first()
    if not sfida:
        raise HTTPException(status_code=404, detail="Sfida non trovata")
    
    if not _utente_puo_vedere(sfida, me, db):
        raise HTTPException(status_code=403, detail="Non hai accesso a questa sfida")

    # Check partecipazione con query diretta — non carica tutto in memoria
    ho_partecipato = db.query(PartecipazioneSfida).filter(
        PartecipazioneSfida.sfida_id == sfida_id,
        PartecipazioneSfida.utente_id == me.id,
    ).first() is not None
    if not ho_partecipato and sfida.autore_id != me.id:
        raise HTTPException(status_code=403, detail="Devi partecipare per vedere le foto")

    return [{
        "id": p.id,
        "utente": _utente_public_response(p.utente, db),
        "foto_profilo_autore": p.utente.foto_profilo,
        "username_autore": p.utente.username,
        "foto_url": p.foto_url,
        "media_voti": p.media_voti,
        "ho_votato": any(v.votante_id == me.id for v in p.voti),
    } for p in sfida.partecipazioni]


# ============================================================
# VOTA
# ============================================================
@router.post("/partecipazioni/{partecipazione_id}/vota")
async def vota_partecipazione(
    partecipazione_id: int,
    dati: RichiestaVoto, # <-- PRIMA ERA: voto: float. ORA USIAMO IL MODELLO!
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    
    if not 0 <= dati.voto <= 10:
        raise HTTPException(400, "Il voto deve essere tra 0 e 10")
    
    p = db.query(PartecipazioneSfida).filter(
        PartecipazioneSfida.id == partecipazione_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Non trovata")

    if p.utente_id == me.id:
        raise HTTPException(status_code=403, detail="Non puoi votare la tua foto")
    
    # Verifica che il votante abbia partecipato alla sfida
    ha_partecipato = db.query(PartecipazioneSfida).filter(
        PartecipazioneSfida.sfida_id == p.sfida_id,
        PartecipazioneSfida.utente_id == me.id,
    ).first()
    if not ha_partecipato:
        raise HTTPException(status_code=403, detail="Devi partecipare per votare")
    
    # Controlla sfida scaduta
    sfida = p.sfida
    if sfida.is_scaduta:
        raise HTTPException(status_code=400, detail="Sfida scaduta, non puoi più votare")

    
    esiste = db.query(VotoSfida).filter(
        VotoSfida.partecipazione_id == partecipazione_id,
        VotoSfida.votante_id == me.id
    ).first()
    if esiste:
        raise HTTPException(status_code=400, detail="Hai già votato")


    # 1. Registriamo il voto
    nuovo_voto = VotoSfida(
        partecipazione_id=partecipazione_id,
        votante_id=me.id,
        voto=dati.voto
    )
    db.add(nuovo_voto)

    # Contatori denormalizzati
    p.somma_voti = (p.somma_voti or 0) + dati.voto
    p.num_voti = (p.num_voti or 0) + 1
    nuova_media = p.media_voti

    # ✨ FIX PER I BADGE DEI VOTI!
    me.voti_dati += 1
    if dati.voto < 5.0:
        me.voti_negativi += 1

    # 3. AGGIORNIAMO LE STATISTICHE DELL'AUTORE (Chi riceve il voto)
    autore_foto = p.utente
    if dati.voto == 10.0:
        autore_foto.ha_preso_dieci = True
    if nuova_media > autore_foto.miglior_media:
        autore_foto.miglior_media = nuova_media

    db.commit()
    # Broadcast real-time
    asyncio.create_task(broadcast_voto(
        sfida_id=p.sfida_id,
        partecipazione_id=partecipazione_id,
        nuova_media=nuova_media,
        num_voti=p.num_voti,
    ))


    return {"media_voti": nuova_media}


# ============================================================
# HELPERS
# ============================================================

def _utente_puo_vedere(sfida: Sfida, utente: Utente, db: Session) -> bool:
    """Regole di visibilità — usa relazioni eager-loaded dove possibile."""
    if sfida.autore_id == utente.id:
        return True

    if sfida.visibilita == "selezionati":
        # Usa la relazione già caricata con joinedload
        return any(inv.invitato_id == utente.id for inv in sfida.inviti)

    # Sfida pubblica — visibile solo a chi segue l'autore
    return any(f.seguito_id == sfida.autore_id for f in utente.seguiti_rel)


def _sfida_response(
    s: Sfida,
    utente_id: int,
    db: Session,
    server_now: datetime | None = None,
) -> dict:
    """Serializza una sfida con i nuovi campi per la fase classifica."""
    ora = server_now or datetime.now(timezone.utc)
    scadenza_utc = s.scadenza.replace(tzinfo=timezone.utc)

    is_scaduta = ora > scadenza_utc
    fine_classifica = scadenza_utc + timedelta(hours=2)
    in_fase_classifica = is_scaduta and ora < fine_classifica

    invitati = []
    if s.visibilita == "selezionati":
        invitati = [_utente_public_response(inv.invitato, db) for inv in s.inviti]

    sono_invitato = any(
        inv.invitato_id == utente_id for inv in s.inviti
    ) if s.visibilita == "selezionati" else False

    partecipazioni_list = []
    for p in s.partecipazioni:
        partecipazioni_list.append({
            "id": p.id,
            "utente": _utente_public_response(p.utente, db),
            "foto_url": p.foto_url,
            "media_voti": p.media_voti,
            "ho_votato": any(v.votante_id == utente_id for v in p.voti),
            "creato_at": p.creato_at.isoformat() if p.creato_at else None,
        })

    # ── Classifica ordinata per media_voti desc ──
    classifica = None
    if in_fase_classifica or is_scaduta:
        classifica = sorted(
            partecipazioni_list,
            key=lambda p: p["media_voti"],
            reverse=True,
        )
        # Aggiungi posizione
        for idx, entry in enumerate(classifica):
            entry["posizione"] = idx + 1

    return {
        "id": s.id,
        "autore": _utente_public_response(s.autore, db),
        "tema": s.tema,
        "durata_ore": s.durata_ore,
        "scadenza": s.scadenza.isoformat(),
        "is_scaduta": is_scaduta,
        "in_fase_classifica": in_fase_classifica,
        "fine_classifica": fine_classifica.isoformat() if in_fase_classifica else None,
        "visibilita": s.visibilita,
        "vincitore": _utente_public_response(s.vincitore, db) if s.vincitore else None,
        "num_partecipanti": len(s.partecipazioni),
        "ho_partecipato": any(p.utente_id == utente_id for p in s.partecipazioni),
        "sono_invitato": sono_invitato,
        "invitati": invitati,
        "creato_at": s.creato_at.isoformat(),
        "partecipazioni": partecipazioni_list,
        "classifica": classifica,
        # ── CLOCK SYNC: il client calcola il delta una sola volta ──
        "server_now": ora.isoformat(),
    }

def _aggiorna_sfide_consecutive(utente: Utente, db: Session):
    """
    Logica identica alla streak post — finestra 24h:
    - Prima sfida → consecutive = 1
    - Sfida entro 24h dall'ultima → aggiorna timer, consecutive invariate
    - Sfida tra 24h e 48h → consecutive + 1
    - Sfida dopo 48h → reset a 1
    """
    ora = datetime.now(timezone.utc)

    if not utente.ultima_sfida_at:
        utente.sfide_consecutive = 1
        utente.ultima_sfida_at = ora
        return

    ultima = utente.ultima_sfida_at
    if ultima.tzinfo is None:
        ultima = ultima.replace(tzinfo=timezone.utc)

    diff_ore = (ora - ultima).total_seconds() / 3600

    if diff_ore < 24:
        # Già partecipato oggi — aggiorna solo il timer
        utente.ultima_sfida_at = ora
    elif diff_ore < 48:
        # Giorno successivo — incrementa consecutiva
        utente.sfide_consecutive += 1
        utente.ultima_sfida_at = ora
    else:
        # Saltato un giorno — reset
        utente.sfide_consecutive = 1
        utente.ultima_sfida_at = ora

# Aggiungere in fondo a sfide.py
def calcola_vincitore_sfide_scadute(db: Session):
    """Chiamato dallo scheduler ogni 5 minuti."""
    ora = datetime.now(timezone.utc)
    sfide_scadute = db.query(Sfida).filter(
        Sfida.scadenza <= ora,
        Sfida.vincitore_id == None,
    ).all()

    for sfida in sfide_scadute:
        if not sfida.partecipazioni:
            continue
        migliore = max(sfida.partecipazioni, key=lambda p: p.media_voti)
        if migliore.media_voti > 0:
            sfida.vincitore_id = migliore.utente_id

    db.commit()