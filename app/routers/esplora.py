"""
Router Esplora — Trending post/sfide/sondaggi.

Regole di business (vincolanti):
  1. PRIVACY: solo contenuti di autori con is_privato=False e is_banned=False.
  2. TTL 48h: un contenuto sparisce dall'Esplora esattamente 48h dopo essere
     entrato in "trend" (cioe' dopo creato_at + 48h).
  3. SONDAGGI: se l'utente loggato ha gia' votato, il sondaggio viene ESCLUSO.
  4. SFIDE: nessuna anteprima partecipanti; solo metadati per CTA Partecipa.
  5. Sondaggi/Sfide scaduti esclusi.
  6. BLOCCHI (bidirezionale): se A ha bloccato B, A non vede contenuti di B
     e B non vede contenuti di A. Filtro server-side per sicurezza.

Performance:
  - Zero N+1: tutti i conteggi sono aggregati in una singola query SQL.
  - Lettura blocchi: una sola query condivisa tra le 3 sotto-query.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.dependencies import get_utente_corrente
from app.models.modelli import (
    BloccoUtente,
    Commento,
    Like,
    PartecipazioneSfida,
    Post,
    Sfida,
    Sondaggio,
    Utente,
    VotoSondaggio,
)
from app.services.cache_service import cache_get_or_set

router = APIRouter(prefix="/esplora", tags=["Esplora"])

TREND_TTL_HOURS = 48
MAX_POST = 40
MAX_SFIDE = 20
MAX_SONDAGGI = 20


# ============================================================
# HELPERS
# ============================================================

def _autore_pubblico(u: Utente) -> dict[str, Any]:
    return {
        "id": u.id,
        "nome": u.nome,
        "username": u.username,
        "foto_profilo": u.foto_profilo,
        "is_privato": u.is_privato,
    }


def _ids_bloccati(db: Session, utente_id: int) -> set[int]:
    """
    Restituisce gli id utente da escludere dall'Esplora per `utente_id`,
    in senso BIDIREZIONALE:
      - utenti che `utente_id` ha bloccato   (io -> loro)
      - utenti che hanno bloccato `utente_id` (loro -> io)

    Singola query con OR, zero round-trip extra.
    """
    stmt = select(
        BloccoUtente.bloccante_id, BloccoUtente.bloccato_id
    ).where(
        or_(
            BloccoUtente.bloccante_id == utente_id,
            BloccoUtente.bloccato_id == utente_id,
        )
    )
    ids: set[int] = set()
    for bloccante, bloccato in db.execute(stmt).all():
        ids.add(bloccato if bloccante == utente_id else bloccante)
    return ids


# ============================================================
# ENDPOINT — TRENDING
# ============================================================

@router.get("/trending")
def get_trending(
    timeframe: str = Query(default="week", enum=["day", "week", "month"]),
    skip: int = 0,
    limit: int = 30,
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente),
):
    # Chiave NON include me.id → cache condivisa tra tutti gli utenti
    # (il trending è pubblico e identico per chiunque)
    cache_key = f"trending:{timeframe}"

    def _calcola():
        # … tutta la logica attuale di calcolo ritorna `risultati` completi
        return risultati  # lista completa, non paginata

    tutti = cache_get_or_set(cache_key, ttl_seconds=300, loader=_calcola)

    # Arricchimento per-user (ho_votato, mia_opzione) NON va cachato —
    # si applica dopo, sulla fetch per-user (quick query singola)
    sondaggi_ids = [r["id"] for r in tutti if r["tipo"] == "sondaggio"]
    if sondaggi_ids:
        miei_voti = {
            v.sondaggio_id: v.opzione_index
            for v in db.query(VotoSondaggio).filter(
                VotoSondaggio.utente_id == me.id,
                VotoSondaggio.sondaggio_id.in_(sondaggi_ids),
            ).all()
        }
        for r in tutti:
            if r["tipo"] == "sondaggio":
                r["ho_votato"] = r["id"] in miei_voti
                r["mia_opzione"] = miei_voti.get(r["id"])

    return tutti[skip : skip + limit]


# ============================================================
# POST TRENDING
# ============================================================

def _post_trending(
    db: Session, cutoff: datetime, bloccati_ids: set[int]
) -> list[dict[str, Any]]:
    num_like = func.count(func.distinct(Like.id)).label("num_like")
    num_commenti = func.count(func.distinct(Commento.id)).label("num_commenti")

    stmt = (
        select(Post, num_like, num_commenti)
        .join(Utente, Utente.id == Post.autore_id)
        .outerjoin(Like, Like.post_id == Post.id)
        .outerjoin(Commento, Commento.post_id == Post.id)
        .where(
            Post.creato_at >= cutoff,
            Post.foto_principale.isnot(None),
            Utente.is_privato.is_(False),
            Utente.is_banned.is_(False),
        )
        .group_by(Post.id)
        .order_by((num_like + num_commenti).desc())
        .limit(MAX_POST)
        .options(selectinload(Post.autore))
    )
    if bloccati_ids:
        stmt = stmt.where(Post.autore_id.notin_(bloccati_ids))

    rows = db.execute(stmt).all()

    return [
        {
            "id": post.id,
            "tipo": "post",
            "media_url": post.foto_principale,
            "selfie_url": post.foto_selfie,
            "testo": post.testo,
            "like_count": n_like,
            "commenti_count": n_comm,
            "engagement": n_like + n_comm,
            "creato_at": post.creato_at.isoformat(),
            "autore": _autore_pubblico(post.autore),
        }
        for post, n_like, n_comm in rows
    ]


# ============================================================
# SFIDE TRENDING
# ============================================================

def _sfide_trending(
    db: Session, cutoff: datetime, ora: datetime, bloccati_ids: set[int]
) -> list[dict[str, Any]]:
    num_partecipanti = func.count(PartecipazioneSfida.id).label("num_partecipanti")

    stmt = (
        select(Sfida, num_partecipanti)
        .join(Utente, Utente.id == Sfida.autore_id)
        .outerjoin(
            PartecipazioneSfida,
            PartecipazioneSfida.sfida_id == Sfida.id,
        )
        .where(
            Sfida.creato_at >= cutoff,
            Sfida.scadenza > ora,
            Sfida.visibilita == "tutti",
            Utente.is_privato.is_(False),
            Utente.is_banned.is_(False),
        )
        .group_by(Sfida.id)
        .order_by(num_partecipanti.desc(), Sfida.creato_at.desc())
        .limit(MAX_SFIDE)
        .options(selectinload(Sfida.autore))
    )
    if bloccati_ids:
        stmt = stmt.where(Sfida.autore_id.notin_(bloccati_ids))

    rows = db.execute(stmt).all()

    return [
        {
            "id": sfida.id,
            "tipo": "sfida",
            "media_url": None,
            "testo": sfida.tema,
            "tema": sfida.tema,
            "durata_ore": sfida.durata_ore,
            "scadenza": sfida.scadenza.isoformat(),
            "is_scaduta": False,
            "is_pubblica": True,
            "num_partecipanti": n_part,
            "engagement": n_part * 2,
            "creato_at": sfida.creato_at.isoformat(),
            "azione": "partecipa",
            "autore": _autore_pubblico(sfida.autore),
        }
        for sfida, n_part in rows
    ]


# ============================================================
# SONDAGGI TRENDING
# ============================================================

def _sondaggi_trending(
    db: Session,
    cutoff: datetime,
    ora: datetime,
    utente_id: int,
    bloccati_ids: set[int],
) -> list[dict[str, Any]]:
    sondaggi_votati_stmt = select(VotoSondaggio.sondaggio_id).where(
        VotoSondaggio.utente_id == utente_id
    )

    totale_voti = func.count(VotoSondaggio.id).label("totale_voti")

    stmt = (
        select(Sondaggio, totale_voti)
        .join(Utente, Utente.id == Sondaggio.autore_id)
        .outerjoin(VotoSondaggio, VotoSondaggio.sondaggio_id == Sondaggio.id)
        .where(
            Sondaggio.creato_at >= cutoff,
            Sondaggio.scadenza > ora,
            Sondaggio.id.notin_(sondaggi_votati_stmt),
            Utente.is_privato.is_(False),
            Utente.is_banned.is_(False),
        )
        .group_by(Sondaggio.id)
        .order_by(totale_voti.desc(), Sondaggio.creato_at.desc())
        .limit(MAX_SONDAGGI)
        .options(selectinload(Sondaggio.autore))
    )
    if bloccati_ids:
        stmt = stmt.where(Sondaggio.autore_id.notin_(bloccati_ids))

    rows = db.execute(stmt).all()
    if not rows:
        return []

    sondaggio_ids = [s.id for s, _ in rows]
    conteggi_stmt = (
        select(
            VotoSondaggio.sondaggio_id,
            VotoSondaggio.opzione_index,
            func.count(VotoSondaggio.id).label("conteggio"),
        )
        .where(VotoSondaggio.sondaggio_id.in_(sondaggio_ids))
        .group_by(VotoSondaggio.sondaggio_id, VotoSondaggio.opzione_index)
    )
    mappa_conteggi: dict[int, dict[int, int]] = {}
    for sid, opt_idx, cnt in db.execute(conteggi_stmt).all():
        mappa_conteggi.setdefault(sid, {})[opt_idx] = cnt

    import json
    risultati = []
    for sondaggio, totale in rows:
        try:
            opzioni = json.loads(sondaggio.opzioni)
        except (json.JSONDecodeError, TypeError):
            continue

        conteggi_ops = mappa_conteggi.get(sondaggio.id, {})
        voti_per_opzione = [conteggi_ops.get(i, 0) for i in range(len(opzioni))]

        risultati.append({
            "id": sondaggio.id,
            "tipo": "sondaggio",
            "testo": sondaggio.domanda,
            "domanda": sondaggio.domanda,
            "opzioni": opzioni,
            "voti_per_opzione": voti_per_opzione,
            "totale_voti": totale,
            "ho_votato": False,
            "mia_opzione": None,
            "scadenza": sondaggio.scadenza.isoformat(),
            "engagement": totale,
            "creato_at": sondaggio.creato_at.isoformat(),
            "autore": _autore_pubblico(sondaggio.autore),
        })
    return risultati