from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.modelli import Utente, Follow, BadgeUtente, Notifica, RichiestaFollow, Post
from app.schemas.schemi import UtenteResponse, AggiornaProfilo, BadgeResponse
from app.dependencies import get_utente_corrente
from app.routers.auth import _utente_response
import aiofiles, os, uuid
from typing import List
 
from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request
from app.services.storage_service import carica_e_comprimi_foto
from app.services.fcm_service import manda_notifica

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/utenti", tags=["Utenti"])
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")


def _trova_utente(username: str, db: Session) -> Utente:
    """Trova un utente per username, normalizzando il @ in input."""
    pulito = username.lstrip("@")
    utente = db.query(Utente).filter(Utente.username == pulito).first()
    if not utente:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    return utente



@router.post("/me/fcm-token")
def salva_token_fcm(
    dati: dict,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    from app.models.modelli import TokenDispositivoFCM
    token = dati.get('token')
    piattaforma = dati.get('piattaforma', 'android')
    if not token:
        raise HTTPException(status_code=400, detail="Token mancante")

    # Cerca per TOKEN (non per utente_id) per evitare UniqueViolation
    esistente = db.query(TokenDispositivoFCM).filter(
        TokenDispositivoFCM.token == token
    ).first()
    if esistente:
        # Aggiorna utente e piattaforma
        esistente.utente_id = me.id
        esistente.piattaforma = piattaforma
    else:
        # Rimuovi vecchi token di questo utente
        db.query(TokenDispositivoFCM).filter(
            TokenDispositivoFCM.utente_id == me.id
        ).delete()
        db.add(TokenDispositivoFCM(
            utente_id=me.id,
            token=token,
            piattaforma=piattaforma,
        ))
    db.commit()
    return {"successo": True}


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

@router.get("/me/richieste-inviate")
def get_richieste_inviate(db: Session = Depends(get_db), me: Utente = Depends(get_utente_corrente)):
    # Cerchiamo tutte le richieste in cui IO sono il richiedente e sono "in_attesa"
    richieste = db.query(RichiestaFollow).filter(
        RichiestaFollow.richiedente_id == me.id,
        RichiestaFollow.stato == "in_attesa"
    ).all()
    
    risultati = []
    for r in richieste:
        # Troviamo l'utente a cui l'abbiamo mandata per prenderne l'username
        destinatario = db.query(Utente).filter(Utente.id == r.destinatario_id).first()
        if destinatario:
            risultati.append({
                "username": destinatario.username
            })
            
    return {"successo": True, "dati": risultati}


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
    # 1. Trova l'utente
    utente = _trova_utente(username, db)
    # 2. Ottieni i dati base (nome, bio, etc.) chiamando la tua funzione esistente
    dati_utente = _utente_response(utente, db)
    # Se _utente_response restituisce un oggetto Pydantic, lo trasformiamo in dizionario
    res = dati_utente.model_dump()
    # 3. ✨ IL PEZZO MANCANTE: Recupera gli ultimi post di questo utente
    # Ordiniamo per data decrescente (i più nuovi in alto)
    post_db = db.query(Post).filter(
        Post.autore_id == utente.id # Assicurati che il campo si chiami 'utente_id' o 'autore_id'
    ).order_by(Post.creato_at.desc()).limit(21).all()
    # 4. Formattiamo i post come si aspetta Flutter
    res["ultimi_post"] = [
        {
            "id": p.id,
            "foto_principale": p.foto_principale,
            "foto_selfie": p.foto_selfie,
            "testo" : p.testo,
            "creato_at": p.creato_at.isoformat() if p.creato_at else None,
            # ✨ STATISTICHE PER FLUTTER
            "num_like" : len(p.like), # Conta quanti like ci sono
            "media_voti" : p.media_voti if p.media_voti is not None else 0.0,
            "num_commenti" : len(p.commenti),
            # Ti serve anche sapere se TU (utente loggato) hai messo like a questo post
            "is_liked": any(l.utente_id == me.id for l in p.like)
        }  for p in post_db
    ]
    return res

# ============================================================
# ELIMINA PROFILO
# ============================================================

@router.delete("/me/account")
def elimina_account(
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    """
    Elimina permanentemente l'account e tutti i dati associati.
    Grazie a cascade="all, delete" nel modello, SQLAlchemy
    cancella automaticamente post, like, commenti, follow, ecc.
    """
    db.delete(me)
    db.commit()
    return {"messaggio": "Account eliminato definitivamente"}

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
    if dati.onboarding_completato is not None:
        me.onboarding_completato = dati.onboarding_completato
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
    # Controllo di sicurezza sul tipo di file
    if foto.content_type and not foto.content_type.startswith("image/"):
        ext = foto.filename.split(".")[-1].lower() if foto.filename else ""
        if ext not in ["jpg", "jpeg", "png", "gif", "webp", "heic"]:
            raise HTTPException(status_code=400, detail="Il file non è un'immagine")

    # CARICAMENTO CLOUD (tramite il nostro nuovo servizio)
    # Passiamo il file e indichiamo che va nella cartella "profili" del bucket
    url_pubblico = await carica_e_comprimi_foto(foto, cartella="profili")

    # Salviamo solo il link nel nostro database
    me.foto_profilo = url_pubblico
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

        manda_notifica(
            db=db,
            destinatario_id=target.id,
            titolo="Nuovo follower! 🌟",
            corpo=f"{me.nome} ha iniziato a seguirti",
        )
        
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

    manda_notifica(
        db=db,
        destinatario_id=target.id,
        titolo="Nuovo follower! 🌟",
        corpo=f"{me.nome} ha iniziato a seguirti",
    )
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
@router.post("/{utente_id}/accetta")
def accetta_richiesta(
    utente_id: int,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    # 1. Cerchiamo la richiesta usando l'ID di chi ce l'ha mandata (utente_id)
    richiesta = db.query(RichiestaFollow).filter(
        RichiestaFollow.richiedente_id == utente_id,
        RichiestaFollow.destinatario_id == me.id,
        RichiestaFollow.stato == "in_attesa"
    ).first()
    
    if not richiesta:
        return {"successo": True, "messaggio": "Già processata"}  # ← invece di raise HTTPException

    # 2. Creiamo il Follow
    follow = Follow(
        follower_id=utente_id,
        seguito_id=me.id
    )
    db.add(follow)
    richiesta.stato = "accettata"
    

    # 4. Inviamo la notifica
    db.add(Notifica(
        destinatario_id=utente_id,
        mittente_id=me.id,
        tipo="follow_accettato",
        testo=f"{me.nome} ha accettato la tua richiesta di follow",
    ))
    
    db.commit()

    manda_notifica(
        db=db,
        destinatario_id=richiesta.richiedente_id,
        titolo="Richiesta accettata! ✅",
        corpo=f"{me.nome} ha accettato la tua richiesta di follow",
    )
    # ✨ Restituiamo successo: True per Flutter
    return {"successo": True, "messaggio": "Richiesta accettata"}


@router.post("/{utente_id}/rifiuta")
def rifiuta_richiesta(
    utente_id: int,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    # Cerchiamo sempre per utente_id
    richiesta = db.query(RichiestaFollow).filter(
        RichiestaFollow.richiedente_id == utente_id,
        RichiestaFollow.destinatario_id == me.id,
        RichiestaFollow.stato == "in_attesa"
    ).first()
    
    if not richiesta:
        raise HTTPException(status_code=404, detail="Richiesta non trovata")

    richiesta.stato = "rifiutata"
    db.commit()
    
    # ✨ Restituiamo successo: True per Flutter
    return {"successo": True, "messaggio": "Richiesta rifiutata"}


# ============================================================
# LISTA SEGUITI DI UN UTENTE — amici che segue un utente (per vedere la sua lista di amici)
# ============================================================

@router.get("/{username}/seguiti")
def get_seguiti_di_utente(username: str, db: Session = Depends(get_db)):
    # 1. Usiamo la tua funzione helper che normalizza già la @ e gestisce il 404!
    utente = _trova_utente(username, db)
    
    # 2.estraiamo gli ID di chi segue dalla TUA relationship esistente (seguiti_rel)
    seguiti_ids = [f.seguito_id for f in utente.seguiti_rel]
    
    if not seguiti_ids:
        return []
        
    # 3. Recuperiamo gli utenti completi dal database
    utenti_seguiti = db.query(Utente).filter(Utente.id.in_(seguiti_ids)).all()
    
    # 4. Usiamo _utente_response per restituire i dati nello stesso esatto
    # formato di Flutter (così è coerente col resto dell'app!)
    return [_utente_response(amico, db) for amico in utenti_seguiti]


# ============================================================
# RICERCA UTENTI — normalizza input
# ============================================================

@router.get("/ricerca/{query}")
@limiter.limit("20/minute") # <--- Aggiungi questo decoratore
def cerca_utenti(
    request: Request,
    query: str,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)):
    # Normalizza: rimuovi @ e spazi
    q = query.strip().lstrip("@")
    # Ritorna il formato standard che Flutter si aspetta se la query è vuota
    if len(q) < 1:
        return []
    # 1) trova gli utenti
    utenti = db.query(Utente).filter(
        (Utente.username.ilike(f"%{q}%")) |
        (Utente.nome.ilike(f"%{q}%"))
    ).limit(20).all()

    risultato_finale = []

    for u in utenti:
        # 2. Ottieni i dati base dell'utente usando la tua funzione esistente
        dati_base = _utente_response(u, db)
        # Se la tua funzione restituisce un modello Pydantic, convertilo in dizionario:
        if hasattr(dati_base, "dict"):
            dati_base = dati_base.dict()
        # 3. Cerca gli ultimi 3 post di questo utente
        ultimi_post = db.query(Post).filter(
            Post.autore_id == u.id
        ).order_by(Post.creato_at.desc()).limit(3).all()
        # 4. Estrai solo i link delle foto (assicurati che il campo si chiami url_foto o adattalo al tuo db)
        urls_foto = [post.foto_principale for post in ultimi_post if post.foto_principale]
        # 5. Inserisci il nuovo campo magico per Flutter
        dati_base["ultime_tre_foto"] = urls_foto

        risultato_finale.append(dati_base)

    # 6. Ritorna il formato esatto che Flutter decodifica ("successo" e "dati")
    return risultato_finale


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
