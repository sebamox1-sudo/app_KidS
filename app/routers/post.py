from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.models.modelli import Post, Like, Voto, Commento, Notifica, Streak
from app.schemas.schemi import PostResponse, VotoPostRequest, CommentoRequest, CommentoResponse
from app.dependencies import get_utente_corrente
from app.models.modelli import Utente
from app.routers.auth import _utente_response
from app.services.badge_service import verifica_badge
import aiofiles, os, uuid
from datetime import datetime, timezone, timedelta

from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request
from app.services.storage_service import carica_e_comprimi_foto
from app.services.fcm_service import manda_notifica

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/post", tags=["Post"])
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")


# ============================================================
# PUBBLICA POST TESTUALE (senza foto obbligatoria)
# ============================================================
@router.post("/testo", response_model=PostResponse, status_code=201)
@limiter.limit("5/minute")
async def pubblica_post_testuale(
    request: Request,
    testo: str = Form(...),
    hashtag: str = Form(""),
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    post = Post(
        autore_id=me.id,
        foto_principale=None,
        foto_selfie=None,
        testo=testo,
        hashtag=hashtag,
        amici_taggati="",
    )
    db.add(post)
    db.flush()

    _aggiorna_streak(me, db)

    for follow in me.follower_rel:
        db.add(Notifica(
            destinatario_id=follow.follower_id,
            mittente_id=me.id,
            tipo="post",
            testo=f"{me.nome} ha scritto un nuovo post",
        ))

    db.commit()
    db.refresh(post)
    await verifica_badge(me, db)
    return _post_response(post, me.id, db)


# ============================================================
# PUBBLICA POST CON FOTO
# ============================================================
@router.post("/", response_model=PostResponse, status_code=201)
@limiter.limit("5/minute") # <--- Il Rate limiting che avevamo aggiunto!
async def pubblica_post(
    request: Request, # <--- Obbligatorio per il Rate Limiting
    foto_principale: Optional[UploadFile] = File(None),
    foto_selfie: Optional[UploadFile] = File(None),
    hashtag: str = Form(""),
    amici_taggati: str = Form(""),
    testo: str = Form(""),
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    # 1. Carica la foto principale nel cloud (se l'utente l'ha inserita)
    url_foto_principale = None
    if foto_principale and foto_principale.filename:
        # Usiamo il nostro motore: comprime a 1440px e spedisce su Supabase
        url_foto_principale = await carica_e_comprimi_foto(foto_principale, cartella="post")

    # 2. Carica il selfie nel cloud (se presente)
    url_foto_selfie = None
    if foto_selfie and foto_selfie.filename:
        url_foto_selfie = await carica_e_comprimi_foto(foto_selfie, cartella="post")

    # 3. Creiamo il Post nel Database usando i link pubblici appena ricevuti!
    post = Post(
        autore_id=me.id,
        foto_principale=url_foto_principale,
        foto_selfie=url_foto_selfie,
        hashtag=hashtag,
        amici_taggati=amici_taggati,
        testo=testo,
    )
    db.add(post)
    db.flush()

    # Aggiorniamo le statistiche e inviamo le notifiche (tuo codice originale)
    _aggiorna_streak(me, db)

    for follow in me.follower_rel:
        notifica = Notifica(
            destinatario_id=follow.follower_id,
            mittente_id=me.id,
            tipo="post",
            testo=f"{me.nome} ha pubblicato un nuovo post",
        )
        db.add(notifica)

    db.commit()
    db.refresh(post)
    await verifica_badge(me, db)
    
    return _post_response(post, me.id, db)


# ============================================================
# FEED — ottimizzato con batch queries per like e voti
# ============================================================
@router.get("/feed", response_model=List[PostResponse])
def get_feed(skip: int = 0, limit: int = 20,
             db: Session = Depends(get_db),
             me: Utente = Depends(get_utente_corrente)):

    seguiti_ids = [f.seguito_id for f in me.seguiti_rel]

    from app.models.modelli import Utente as UtenteModel
    autori_visibili = (
        db.query(UtenteModel.id)
        .filter(
            (UtenteModel.is_privato == False) |
            (UtenteModel.id.in_(seguiti_ids)) |
            (UtenteModel.id == me.id)
        )
        .all()
    )
    autori_ids = [u.id for u in autori_visibili]

    ventiquattro_ore_fa = datetime.now(timezone.utc) - timedelta(hours=24)

    post = db.query(Post).filter(
    Post.autore_id.in_(autori_ids),
    Post.creato_at > ventiquattro_ore_fa  # ← solo ultimi 24h
    ).order_by(Post.creato_at.desc()).offset(skip).limit(limit).all()

    if not post:
        return []

    # ── BATCH: prendi tutti i like e voti dell'utente in UNA query ──
    post_ids = [p.id for p in post]

    # Set di post_id che l'utente ha likato
    miei_like = set(
        row.post_id for row in
        db.query(Like.post_id).filter(
            Like.utente_id == me.id,
            Like.post_id.in_(post_ids)
        ).all()
    )

    # Dict post_id → voto dell'utente
    miei_voti = {
        row.post_id: row.voto for row in
        db.query(Voto.post_id, Voto.voto).filter(
            Voto.utente_id == me.id,
            Voto.post_id.in_(post_ids)
        ).all()
    }

    return [
        _post_response_batch(p, miei_like, miei_voti, db)
        for p in post
    ]


# ============================================================
# LIKE / UNLIKE
# ============================================================
@router.post("/{post_id}/like")
def metti_like(
    post_id: int, 
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
    ):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post non trovato")
    # Controlla se il like esiste già. 
    # ATTENZIONE: Usa 'follower_id' o 'utente_id' in base a come lo hai chiamato nel modello Like
    esiste = db.query(Like).filter(
        Like.utente_id == me.id, 
        Like.post_id == post_id
        ).first()
    if esiste:
        raise HTTPException(status_code=400, detail="Like già messo")

    like = Like(utente_id=me.id, post_id=post_id)
    db.add(like)

    # ✨ AGGIORNAMENTO BADGE: Chi riceve il like diventa più popolare!
    if post.autore:
        post.autore.like_ricevuti += 1

    if post.autore_id != me.id:
        db.add(Notifica(
            destinatario_id=post.autore_id,
            mittente_id=me.id,
            tipo="like",
            testo=f"{me.nome} ha messo like al tuo post",
        )) 

    db.commit()
    if post.autore_id != me.id:
        manda_notifica(
             db=db,
            destinatario_id=post.autore_id,
            titolo="Nuovo like! ❤️",
            corpo=f"{me.nome} ha messo like al tuo post",
        )
    return {"num_like": post.num_like}


@router.delete("/{post_id}/like")
def togli_like(post_id: int, db: Session = Depends(get_db),
               me: Utente = Depends(get_utente_corrente)):
    like = db.query(Like).filter(
        Like.utente_id == me.id, Like.post_id == post_id).first()
    if not like:
        raise HTTPException(status_code=404, detail="Like non trovato")
    
    # ✨ SCALIAMO IL LIKE:
    if like.post.autore:
        like.post.autore.like_ricevuti -= 1

    db.delete(like)
    db.commit()
    return {"messaggio": "Like rimosso"}


# ============================================================
# VOTO ANONIMO
# ============================================================
@router.post("/{post_id}/vota")
async def vota_post(post_id: int, dati: VotoPostRequest,
               db: Session = Depends(get_db),
               me: Utente = Depends(get_utente_corrente)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post non trovato")

    esiste = db.query(Voto).filter(
        Voto.utente_id == me.id, Voto.post_id == post_id).first()
    if esiste:
        raise HTTPException(status_code=400, detail="Hai già votato")

    voto = Voto(
        utente_id=me.id,
        post_id=post_id,
        voto=dati.voto,
        anonimo=dati.anonimo,
    )
    db.add(voto)

    # ✨ AGGIORNAMENTO BADGE: Incrementiamo i tuoi voti dati
    me.voti_dati += 1
    if dati.voto < 5:
        me.voti_negativi += 1 # Per il badge "Occhio Fino"

    if post.autore_id != me.id:
        testo = (
            f"Un utente anonimo ha votato il tuo post con {dati.voto:.1f}"
            if dati.anonimo
            else f"{me.nome} ha votato il tuo post con {dati.voto:.1f}"
        )
        db.add(Notifica(
            destinatario_id=post.autore_id,
            mittente_id=None if dati.anonimo else me.id,
            tipo="voto",
            testo=testo,
        ))

    db.commit()

    # Push anonima — non rivela mai chi ha votato
    if post.autore_id != me.id:
        manda_notifica(
            db=db,
            destinatario_id=post.autore_id,
            titolo="Nuovo voto! ⭐",
            corpo=f"Il tuo post ha ricevuto un voto di {dati.voto:.1f}",
            # Nessun nome — sempre anonima
        )

    await verifica_badge(me, db, voto_negativo=dati.voto < 5)
    return {"media_voti": post.media_voti}


# ============================================================
# COMMENTI
# ============================================================
@router.get("/{post_id}/commenti", response_model=List[CommentoResponse])
def get_commenti(post_id: int, db: Session = Depends(get_db),
                 me: Utente = Depends(get_utente_corrente)):
    commenti = db.query(Commento).filter(
        Commento.post_id == post_id,
        Commento.risposta_a_id == None
    ).order_by(Commento.creato_at).all()
    return [_commento_response(c, db) for c in commenti]


@router.post("/{post_id}/commenti", response_model=CommentoResponse, status_code=201)
@limiter.limit("10/minute")
async def aggiungi_commento(
    request: Request,
    post_id: int, dati: CommentoRequest,
    db: Session = Depends(get_db),                   
    me: Utente = Depends(get_utente_corrente)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post non trovato")

    commento = Commento(
        autore_id=me.id,
        post_id=post_id,
        testo=dati.testo,
        risposta_a_id=dati.risposta_a_id,
    )
    db.add(commento)

    # ✨ AGGIORNAMENTO BADGE: Sei un utente attivo nei commenti!
    me.commenti_scritti += 1

    if post.autore_id != me.id:
        db.add(Notifica(
            destinatario_id=post.autore_id,
            mittente_id=me.id,
            tipo="commento",
            testo=f"{me.nome} ha commentato: \"{dati.testo[:50]}\"",
        ))

    db.commit()
    db.refresh(commento)

    # ✨ Push notification
    if post.autore_id != me.id:
        manda_notifica(
            db=db,
            destinatario_id=post.autore_id,
            titolo="Nuovo commento! 💬",
            corpo=f"{me.nome}: {dati.testo[:50]}",
        )
    
    await verifica_badge(me, db, nuovo_commento=True)
    return _commento_response(commento, db)


# ============================================================
# ELIMINA POST — ora elimina anche i file dal filesystem
# ============================================================
@router.delete("/{post_id}")
def elimina_post(post_id: int, db: Session = Depends(get_db),
                 me: Utente = Depends(get_utente_corrente)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post non trovato")
    if post.autore_id != me.id:
        raise HTTPException(status_code=403, detail="Non puoi eliminare questo post")

    # Elimina file dal filesystem
    _elimina_file_post(post)

    db.delete(post)
    db.commit()
    return {"messaggio": "Post eliminato"}

# ============================================================
# ESPLORA HASHTAG
# ============================================================
@router.get("/esplora/hashtag/{tag}", response_model=List[PostResponse])
@limiter.limit("20/minute")
def esplora_hashtag(
    request: Request,
    tag: str, 
    skip: int = 0, 
    limit: int = 30,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente)
):
    # Puliamo il tag (togliamo il # se l'utente lo ha inserito nell'URL)
    tag_pulito = tag.replace("#", "").strip()

    if not tag_pulito:
        return []

    # Cerchiamo tutti i post pubblici (o dei nostri amici) che contengono l'hashtag
    from app.models.modelli import Utente as UtenteModel
    
    post_trovati = db.query(Post).join(UtenteModel).filter(
        UtenteModel.is_privato == False, # Mostriamo solo post di account pubblici
        Post.hashtag.ilike(f"%{tag_pulito}%")
    ).order_by(Post.creato_at.desc()).offset(skip).limit(limit).all()

    if not post_trovati:
        return []

    # ── BATCH: Precarichiamo like e voti per non far crashare il server ──
    post_ids = [p.id for p in post_trovati]

    miei_like = set(
        row.post_id for row in
        db.query(Like.post_id).filter(
            Like.utente_id == me.id,
            Like.post_id.in_(post_ids)
        ).all()
    )

    miei_voti = {
        row.post_id: row.voto for row in
        db.query(Voto.post_id, Voto.voto).filter(
            Voto.utente_id == me.id,
            Voto.post_id.in_(post_ids)
        ).all()
    }

    return [
        _post_response_batch(p, miei_like, miei_voti, db)
        for p in post_trovati
    ]


# ============================================================
# HELPERS
# ============================================================

def _elimina_file_post(post: Post):
    """
    I file ora sono su Supabase. 
    Per ora lasciamo che restino nel bucket cloud, 
    oppure in futuro aggiungeremo una chiamata per cancellarli da lì.
    """
    pass


def _post_response(post: Post, utente_id: int, db: Session) -> PostResponse:
    """Risposta post singolo — usa query individuali (ok per singoli post)."""
    ho_messo_like = db.query(Like).filter(
        Like.utente_id == utente_id, Like.post_id == post.id).first() is not None
    mio_voto_obj = db.query(Voto).filter(
        Voto.utente_id == utente_id, Voto.post_id == post.id).first()

    return PostResponse(
        id=post.id,
        autore=_utente_response(post.autore, db),
        foto_principale=post.foto_principale,
        foto_selfie=post.foto_selfie,
        testo=post.testo,
        hashtag=post.hashtag or "",
        num_like=post.num_like,
        media_voti=post.media_voti,
        ho_messo_like=ho_messo_like,
        ho_votato=mio_voto_obj is not None,
        mio_voto=mio_voto_obj.voto if mio_voto_obj else None,
        creato_at=post.creato_at,
    )


def _post_response_batch(
    post: Post,
    miei_like: set,
    miei_voti: dict,
    db: Session,
) -> PostResponse:
    """Risposta post per il feed — usa dati pre-caricati in batch (zero query extra)."""
    return PostResponse(
        id=post.id,
        autore=_utente_response(post.autore, db),
        foto_principale=post.foto_principale,
        foto_selfie=post.foto_selfie,
        testo=post.testo,
        hashtag=post.hashtag or "",
        num_like=post.num_like,
        media_voti=post.media_voti,
        ho_messo_like=post.id in miei_like,
        ho_votato=post.id in miei_voti,
        mio_voto=miei_voti.get(post.id),
        creato_at=post.creato_at,
    )


def _commento_response(c: Commento, db: Session) -> CommentoResponse:
    return CommentoResponse(
        id=c.id,
        autore=_utente_response(c.autore, db),
        testo=c.testo,
        risposta_a_id=c.risposta_a_id,
        risposte=[_commento_response(r, db) for r in (c.risposte or [])],  
        creato_at=c.creato_at,
    )


def _aggiorna_streak(utente: Utente, db: Session):
    """Aggiorna streak basandosi sulla data locale (confronto per giorno calendario)."""
    streak = utente.streak
    if not streak:
        streak = Streak(utente_id=utente.id, giorni=1)
        streak.ultimo_post = datetime.now(timezone.utc)
        db.add(streak)
        return

    ora = datetime.now(timezone.utc)

    if streak.ultimo_post:
        ultimo = streak.ultimo_post.replace(tzinfo=timezone.utc) if streak.ultimo_post.tzinfo is None else streak.ultimo_post
        # Confronta per data calendario (non ore)
        oggi = ora.date()
        ultimo_giorno = ultimo.date()
        diff_giorni = (oggi - ultimo_giorno).days

        if diff_giorni == 0:
            pass  # Già postato oggi
        elif diff_giorni == 1:
            streak.giorni += 1
            if streak.giorni > streak.record:
                streak.record = streak.giorni
        else:
            streak.giorni = 1  # Reset — più di 1 giorno senza post
    else:
        streak.giorni = 1

    streak.ultimo_post = ora