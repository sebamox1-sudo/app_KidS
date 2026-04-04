from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime, timezone, timedelta
from typing import List
from app.database import get_db
from app.models.modelli import Post, Sondaggio, Sfida, Like, Utente, Commento
from app.dependencies import get_utente_corrente
from app.routers.auth import _utente_response

router = APIRouter(prefix="/esplora", tags=["Esplora"])

# ============================================================
# TRENDING — post, sfide, sondaggi con più engagement
# Solo contenuti da profili pubblici
# ============================================================

@router.get("/trending")
def get_trending(
    timeframe: str = Query(default="week", enum=["day", "week", "month"]),
    skip: int = 0,
    limit: int = 30,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente),
):
    # Calcola la finestra temporale
    ora = datetime.now(timezone.utc)
    delta = {
        "day": timedelta(days=1),
        "week": timedelta(days=7),
        "month": timedelta(days=30),
    }[timeframe]
    da = ora - delta

    risultati = []

    # ── POST TRENDING ────────────────────────────────────────
    # Conta like + commenti per engagement score
    # Filtra solo autori pubblici
    post_trending = (
        db.query(
            Post,
            func.count(Like.id).label("num_like"),
        )
        .join(Utente, Utente.id == Post.autore_id)
        .outerjoin(Like, Like.post_id == Post.id)
        .filter(
            Post.creato_at >= da,
            Utente.is_privato == False,
            Utente.is_banned == False,
            Post.foto_principale != None,  # solo post con foto
        )
        .group_by(Post.id)
        .order_by(desc("num_like"))
        .limit(limit)
        .all()
    )

    for post, num_like in post_trending:
        risultati.append({
            "id": post.id,
            "tipo": "post",
            "media_url": post.foto_principale,
            "selfie_url": post.foto_selfie,
            "testo": post.testo,
            "like_count": num_like,
            "commenti_count": len(post.commenti),
            "engagement": num_like + len(post.commenti),
            "creato_at": post.creato_at.isoformat(),
            "autore": {
                "id": post.autore.id,
                "nome": post.autore.nome,
                "username": post.autore.username,
                "foto_profilo": post.autore.foto_profilo,
                "is_privato": post.autore.is_privato,
            },
        })

    # ── SFIDE TRENDING ───────────────────────────────────────
    sfide_trending = (
        db.query(Sfida)
        .join(Utente, Utente.id == Sfida.autore_id)
        .filter(
            Sfida.creato_at >= da,
            Utente.is_privato == False,
            Utente.is_banned == False,
        )
        .order_by(desc(Sfida.creato_at))
        .limit(10)
        .all()
    )

    for sfida in sfide_trending:
        num_partecipanti = len(sfida.partecipazioni)
        risultati.append({
            "id": sfida.id,
            "tipo": "sfida",
            "media_url": sfida.partecipazioni[0].foto_url
                if sfida.partecipazioni else None,
            "testo": sfida.tema,
            "like_count": 0,
            "engagement": num_partecipanti * 2,
            "num_partecipanti": num_partecipanti,
            "scadenza": sfida.scadenza.isoformat(),
            "is_scaduta": sfida.is_scaduta,
            "creato_at": sfida.creato_at.isoformat(),
            "autore": {
                "id": sfida.autore.id,
                "nome": sfida.autore.nome,
                "username": sfida.autore.username,
                "foto_profilo": sfida.autore.foto_profilo,
                "is_privato": sfida.autore.is_privato,
            },
        })

    # ── SONDAGGI TRENDING ────────────────────────────────────
    sondaggi_trending = (
        db.query(Sondaggio)
        .join(Utente, Utente.id == Sondaggio.autore_id)
        .filter(
            Sondaggio.creato_at >= da,
            Utente.is_privato == False,
            Utente.is_banned == False,
        )
        .order_by(desc(Sondaggio.creato_at))
        .limit(10)
        .all()
    )

    for sondaggio in sondaggi_trending:
        num_voti = len(sondaggio.voti)
        risultati.append({
            "id": sondaggio.id,
            "tipo": "sondaggio",
            "media_url": None,
            "testo": sondaggio.domanda,
            "like_count": 0,
            "engagement": num_voti,
            "totale_voti": num_voti,
            "creato_at": sondaggio.creato_at.isoformat(),
            "autore": {
                "id": sondaggio.autore.id,
                "nome": sondaggio.autore.nome,
                "username": sondaggio.autore.username,
                "foto_profilo": sondaggio.autore.foto_profilo,
                "is_privato": sondaggio.autore.is_privato,
            },
        })

    # ── ORDINA PER ENGAGEMENT ────────────────────────────────
    risultati.sort(key=lambda x: x["engagement"], reverse=True)

    # ── PAGINAZIONE ──────────────────────────────────────────
    return risultati[skip: skip + limit]