from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app.dependencies import get_utente_corrente
from app.models.modelli import Utente, BloccoUtente, Segnalazione

router = APIRouter(tags=["Blocco & Segnalazioni"])


# ── SCHEMA SEGNALAZIONE ───────────────────────────────────────
class SegnalazioneRequest(BaseModel):
    post_id: Optional[int] = None
    utente_segnalato_id: Optional[int] = None
    motivo: str  # "spam" | "nudita" | "violenza" | "altro"


# ── HELPER: IDs bloccati reciprocamente ───────────────────────
def get_ids_bloccati(me: Utente, db: Session) -> set:
    """
    Ritorna gli ID di tutti gli utenti in relazione di blocco con me
    (sia quelli che ho bloccato io, sia quelli che mi hanno bloccato).
    """
    bloccati_da_me = {
        b.bloccato_id for b in db.query(BloccoUtente).filter(
            BloccoUtente.bloccante_id == me.id
        ).all()
    }
    mi_hanno_bloccato = {
        b.bloccante_id for b in db.query(BloccoUtente).filter(
            BloccoUtente.bloccato_id == me.id
        ).all()
    }
    return bloccati_da_me | mi_hanno_bloccato


# ── TOGGLE BLOCCO ─────────────────────────────────────────────
@router.post("/utenti/{username}/blocca")
def toggle_blocco(
    username: str,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente),
):
    """
    Toggle blocco: se già bloccato lo sblocca, altrimenti lo blocca.
    Il blocco è reciproco — nessuno dei due vede l'altro.
    """
    # Trova l'utente da bloccare
    target = db.query(Utente).filter(Utente.username == username).first()
    if not target:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    if target.id == me.id:
        raise HTTPException(status_code=400, detail="Non puoi bloccare te stesso")

    # Controlla se esiste già il blocco
    blocco_esistente = db.query(BloccoUtente).filter(
        BloccoUtente.bloccante_id == me.id,
        BloccoUtente.bloccato_id == target.id,
    ).first()

    if blocco_esistente:
        # Sblocca
        db.delete(blocco_esistente)
        db.commit()
        return {"messaggio": f"Hai sbloccato @{username}", "bloccato": False}
    else:
        # Blocca
        nuovo_blocco = BloccoUtente(
            bloccante_id=me.id,
            bloccato_id=target.id,
        )
        db.add(nuovo_blocco)
        db.commit()
        return {"messaggio": f"Hai bloccato @{username}", "bloccato": True}


# ── SEGNALAZIONE ──────────────────────────────────────────────
@router.post("/segnalazioni")
def crea_segnalazione(
    dati: SegnalazioneRequest,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente),
):
    """
    Crea una segnalazione per un post o un utente.
    Almeno uno tra post_id e utente_segnalato_id deve essere presente.
    """
    if not dati.post_id and not dati.utente_segnalato_id:
        raise HTTPException(
            status_code=400,
            detail="Specifica post_id o utente_segnalato_id",
        )

    motivi_validi = {"spam", "nudita", "violenza", "molestie", "altro"}
    if dati.motivo not in motivi_validi:
        raise HTTPException(status_code=400, detail="Motivo non valido")

    segnalazione = Segnalazione(
        segnalatore_id=me.id,
        post_id=dati.post_id,
        utente_segnalato_id=dati.utente_segnalato_id,
        motivo=dati.motivo,
    )
    db.add(segnalazione)
    db.commit()
    return {"messaggio": "Segnalazione inviata. Grazie per il tuo contributo."}