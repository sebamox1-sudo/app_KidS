from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.modelli import Utente, Follow, BadgeUtente, Notifica, RichiestaFollow
from app.schemas.schemi import UtenteResponse, AggiornaProfilo, BadgeResponse
from app.dependencies import get_utente_corrente
from app.routers.auth import _utente_response
import aiofiles, os, uuid
from typing import List

router = APIRouter(prefix="/utenti", tags=["Utenti"])
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")


def _trova_utente(username: str, db: Session) -> Utente:
    """Trova un utente per username, normalizzando il @ in input."""
    pulito = username.lstrip("@")
    utente = db.query(Utente).filter(Utente.username == pulito).first()
    if not utente:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    return utente


# ============================================================
# PROFILO UTENTE
# ============================================================
@router.get("/me/richieste", response_model=List[dict])
def get_richieste_ricevute(
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    """Lista richieste di follow ricevute in attesa."""
    richieste = db.query(RichiestaFollow).filter(
        RichiestaFollow.destinatario_id == me.id,
        RichiestaFollow.stato == "in_attesa"
    ).all()
    return [{
        "id": r.id,
        "richiedente": _utente_response(r.richiedente, db),
        "creato_at": r.creato_at,
    } for r in richieste]


@router.get("/me/seguiti", response_model=List[UtenteResponse])
def get_miei_seguiti(
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    """Lista utenti che io seguo — usata per selezionare amici nelle sfide."""
    seguiti_ids = [f.seguito_id for f in me.seguiti_rel]
    utenti = db.query(Utente).filter(Utente.id.in_(seguiti_ids)).all()
    return [_utente_response(u, db) for u in utenti]


@router.get("/{username}", response_model=UtenteResponse)
def get_profilo(username: str, db: Session = Depends(get_db),
                me: Utente = Depends(get_utente_corrente)):
    utente = _trova_utente(username, db)
    return _utente_response(utente, db)


# ============================================================
# AGGIORNA PROFILO
# ============================================================
@router.patch("/me/profilo", response_model=UtenteResponse)
def aggiorna_profilo(dati: AggiornaProfilo, db: Session = Depends(get_db),
                     me: Utente = Depends(get_utente_corrente)):
    if dati.nome is not None:
        me.nome = dati.nome
    if dati.username is not None:
        # Normalizza: rimuovi @ e lowercase
        nuovo_username = dati.username.strip().lstrip("@").lower()
        esistente = db.query(Utente).filter(
            Utente.username == nuovo_username,
            Utente.id != me.id
        ).first()
        if esistente:
            raise HTTPException(status_code=400, detail="Username già in uso")
        me.username = nuovo_username
    if dati.bio is not None:
        me.bio = dati.bio
    if dati.is_privato is not None:
        me.is_privato = dati.is_privato
    db.commit()
    db.refresh(me)
    return _utente_response(me, db)


# ============================================================
# UPLOAD FOTO PROFILO
# ============================================================
@router.post("/me/foto", response_model=UtenteResponse)
async def upload_foto_profilo(
    foto: UploadFile = File(...),
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    if foto.content_type and not foto.content_type.startswith("image/"):
        ext = foto.filename.split(".")[-1].lower() if foto.filename else ""
        if ext not in ["jpg", "jpeg", "png", "gif", "webp", "heic"]:
            raise HTTPException(status_code=400, detail="File non è un'immagine")

    os.makedirs(f"{UPLOAD_DIR}/profili", exist_ok=True)

    # Elimina vecchia foto se esiste
    if me.foto_profilo:
        vecchio = me.foto_profilo.lstrip("/")
        try:
            if os.path.exists(vecchio):
                os.remove(vecchio)
        except OSError:
            pass

    ext = foto.filename.split(".")[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    filepath = f"{UPLOAD_DIR}/profili/{filename}"
    async with aiofiles.open(filepath, "wb") as f:
        await f.write(await foto.read())
    me.foto_profilo = f"/uploads/profili/{filename}"
    db.commit()
    db.refresh(me)
    return _utente_response(me, db)


# ============================================================
# FOLLOW / UNFOLLOW / RICHIESTA
# ============================================================
@router.post("/{username}/segui")
def segui(username: str, db: Session = Depends(get_db),
          me: Utente = Depends(get_utente_corrente)):
    target = _trova_utente(username, db)
    if target.id == me.id:
        raise HTTPException(status_code=400, detail="Non puoi seguire te stesso")

    esiste_follow = db.query(Follow).filter(
        Follow.follower_id == me.id,
        Follow.seguito_id == target.id
    ).first()
    if esiste_follow:
        raise HTTPException(status_code=400, detail="Stai già seguendo questo utente")

    # Account privato → manda richiesta
    if target.is_privato:
        esiste_richiesta = db.query(RichiestaFollow).filter(
            RichiestaFollow.richiedente_id == me.id,
            RichiestaFollow.destinatario_id == target.id,
            RichiestaFollow.stato == "in_attesa"
        ).first()
        if esiste_richiesta:
            raise HTTPException(status_code=400, detail="Richiesta già inviata")

        richiesta = RichiestaFollow(
            richiedente_id=me.id,
            destinatario_id=target.id,
        )
        db.add(richiesta)
        db.add(Notifica(
            destinatario_id=target.id,
            mittente_id=me.id,
            tipo="richiesta_follow",
            testo=f"{me.nome} vuole seguirti",
        ))
        db.commit()
        return {"messaggio": "Richiesta inviata", "stato": "richiesta_inviata"}

    # Account pubblico → segui direttamente
    follow = Follow(follower_id=me.id, seguito_id=target.id)
    db.add(follow)
    db.add(Notifica(
        destinatario_id=target.id,
        mittente_id=me.id,
        tipo="follow",
        testo=f"{me.nome} ha iniziato a seguirti",
    ))
    db.commit()
    return {"messaggio": f"Ora segui {target.username}", "stato": "seguito"}


@router.delete("/{username}/segui")
def smetti_di_seguire(username: str, db: Session = Depends(get_db),
                       me: Utente = Depends(get_utente_corrente)):
    target = _trova_utente(username, db)

    follow = db.query(Follow).filter(
        Follow.follower_id == me.id,
        Follow.seguito_id == target.id
    ).first()
    if follow:
        db.delete(follow)
        db.commit()
        return {"messaggio": f"Hai smesso di seguire {target.username}"}

    richiesta = db.query(RichiestaFollow).filter(
        RichiestaFollow.richiedente_id == me.id,
        RichiestaFollow.destinatario_id == target.id,
        RichiestaFollow.stato == "in_attesa"
    ).first()
    if richiesta:
        db.delete(richiesta)
        db.commit()
        return {"messaggio": "Richiesta annullata"}

    raise HTTPException(status_code=400, detail="Non stai seguendo questo utente")


# ============================================================
# ACCETTA / RIFIUTA RICHIESTA
# ============================================================
@router.post("/richieste/{richiesta_id}/accetta")
def accetta_richiesta(
    richiesta_id: int,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    richiesta = db.query(RichiestaFollow).filter(
        RichiestaFollow.id == richiesta_id,
        RichiestaFollow.destinatario_id == me.id,
        RichiestaFollow.stato == "in_attesa"
    ).first()
    if not richiesta:
        raise HTTPException(status_code=404, detail="Richiesta non trovata")

    follow = Follow(
        follower_id=richiesta.richiedente_id,
        seguito_id=me.id
    )
    db.add(follow)
    richiesta.stato = "accettata"

    db.add(Notifica(
        destinatario_id=richiesta.richiedente_id,
        mittente_id=me.id,
        tipo="follow_accettato",
        testo=f"{me.nome} ha accettato la tua richiesta di follow",
    ))
    db.commit()
    return {"messaggio": "Richiesta accettata"}


@router.post("/richieste/{richiesta_id}/rifiuta")
def rifiuta_richiesta(
    richiesta_id: int,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    richiesta = db.query(RichiestaFollow).filter(
        RichiestaFollow.id == richiesta_id,
        RichiestaFollow.destinatario_id == me.id,
        RichiestaFollow.stato == "in_attesa"
    ).first()
    if not richiesta:
        raise HTTPException(status_code=404, detail="Richiesta non trovata")

    richiesta.stato = "rifiutata"
    db.commit()
    return {"messaggio": "Richiesta rifiutata"}


# ============================================================
# LISTA SEGUITI — amici che seguo (per selettore sfida)
# ============================================================
@router.get("/me/seguiti", response_model=List[UtenteResponse])
def get_miei_seguiti(
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    """Lista utenti che seguo — usata nel selettore amici per sfide."""
    seguiti_ids = [f.seguito_id for f in me.seguiti_rel]
    if not seguiti_ids:
        return []
    utenti = db.query(Utente).filter(Utente.id.in_(seguiti_ids)).all()
    return [_utente_response(u, db) for u in utenti]


# ============================================================
# RICERCA UTENTI — normalizza input
# ============================================================
@router.get("/ricerca/{query}", response_model=List[UtenteResponse])
def cerca_utenti(query: str, db: Session = Depends(get_db),
                 me: Utente = Depends(get_utente_corrente)):
    # Normalizza: rimuovi @ e spazi
    q = query.strip().lstrip("@")
    if len(q) < 1:
        return []

    utenti = db.query(Utente).filter(
        (Utente.username.ilike(f"%{q}%")) |
        (Utente.nome.ilike(f"%{q}%"))
    ).limit(20).all()
    return [_utente_response(u, db) for u in utenti]


# ============================================================
# BADGE UTENTE
# ============================================================
@router.get("/{username}/badge", response_model=List[BadgeResponse])
def get_badge(username: str, db: Session = Depends(get_db),
              me: Utente = Depends(get_utente_corrente)):
    utente = _trova_utente(username, db)
    return utente.badge


# ============================================================
# STATO FOLLOW
# ============================================================
@router.get("/{username}/stato-follow")
def stato_follow(username: str, db: Session = Depends(get_db),
                 me: Utente = Depends(get_utente_corrente)):
    target = _trova_utente(username, db)

    follow = db.query(Follow).filter(
        Follow.follower_id == me.id,
        Follow.seguito_id == target.id
    ).first()
    if follow:
        return {"stato": "seguito"}

    richiesta = db.query(RichiestaFollow).filter(
        RichiestaFollow.richiedente_id == me.id,
        RichiestaFollow.destinatario_id == target.id,
        RichiestaFollow.stato == "in_attesa"
    ).first()
    if richiesta:
        return {"stato": "richiesta_inviata"}

    return {"stato": "nessuno"}