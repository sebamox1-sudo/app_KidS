"""
Router Esplora — Trending post/sfide/sondaggi.

Regole di business (vincolanti):
  1. PRIVACY: solo contenuti di autori con is_privato=False e is_banned=False.
  2. TTL 48h: un contenuto sparisce dall'Esplora esattamente 48h dopo essere
     entrato in "trend" (cioè dopo creato_at + 48h).
  3. SONDAGGI: se l'utente loggato ha gia' votato, il sondaggio viene ESCLUSO
     dalla risposta (non mostrato piu' in Esplora).
  4. SFIDE: la risposta non include anteprime dei partecipanti; fornisce solo
     i metadati necessari per mostrare il CTA "Partecipa/Scatta".
  5. Sondaggi/Sfide scaduti (scadenza < now) vengono esclusi.

Performance:
  - Zero N+1: tutti i conteggi sono aggregati in una singola query SQL.
  - Paginazione applicata lato DB dove possibile (post), in-memory solo per
     il merge finale ordinato per engagement.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.dependencies import get_utente_corrente
from app.models.modelli import (
    Commento,
    Like,
    PartecipazioneSfida,
    Post,
    Sfida,
    Sondaggio,
    Utente,
    VotoSondaggio,
)

router = APIRouter(prefix="/esplora", tags=["Esplora"])

# ── COSTANTI DI BUSINESS ────────────────────────────────────────────
# TTL hard-coded dal product requirement: un contenuto resta in Esplora
# 48h dall'ingresso in trend (= dalla creazione).
TREND_TTL_HOURS = 48

# Cap massimo per singola categoria prima del merge.
MAX_POST = 40
MAX_SFIDE = 20
MAX_SONDAGGI = 20


# ============================================================
# HELPERS — serializzazione autore
# ============================================================

def _autore_pubblico(u: Utente) -> dict[str, Any]:
    """Serializza solo i campi pubblici dell'autore (no email, no is_banned)."""
    return {
        "id": u.id,
        "nome": u.nome,
        "username": u.username,
        "foto_profilo": u.foto_profilo,
        "is_privato": u.is_privato,
    }


# ============================================================
# ENDPOINT — TRENDING
# ============================================================

@router.get("/trending")
def get_trending(
    timeframe: str = Query(default="week", pattern="^(day|week|month)$"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=50),
    db: Session = Depends(get_db),
    me: Utente = Depends(get_utente_corrente),
) -> list[dict[str, Any]]:
    """
    Restituisce i contenuti in trend filtrati per:
      - privacy autore (solo pubblici, non bannati)
      - TTL 48h (creato_at >= now - 48h)
      - sondaggi non gia' votati da `me`
      - sfide/sondaggi non scaduti
    """
    ora = datetime.now(timezone.utc)
    # Il TTL dei trend domina SEMPRE sul timeframe richiesto.
    # Il timeframe puo' solo restringere ulteriormente la finestra.
    ttl_cutoff = ora - timedelta(hours=TREND_TTL_HOURS)
    timeframe_delta = {
        "day": timedelta(days=1),
        "week": timedelta(days=7),
        "month": timedelta(days=30),
    }[timeframe]
    timeframe_cutoff = ora - timeframe_delta

    # Cutoff effettivo = il piu' restrittivo tra TTL e timeframe.
    cutoff = max(ttl_cutoff, timeframe_cutoff)

    risultati: list[dict[str, Any]] = []
    risultati.extend(_post_trending(db, cutoff))
    risultati.extend(_sfide_trending(db, cutoff, ora))
    risultati.extend(_sondaggi_trending(db, cutoff, ora, me.id))

    # Ordina per engagement decrescente e applica paginazione in-memory
    # (il dataset e' gia' capped via MAX_*, ordinamento su max ~80 elementi).
    risultati.sort(key=lambda x: x["engagement"], reverse=True)
    return risultati[skip : skip + limit]


# ============================================================
# QUERY — POST TRENDING (zero N+1)
# ============================================================

def _post_trending(db: Session, cutoff: datetime) -> list[dict[str, Any]]:
    """
    Post trending:
      - autore pubblico e non bannato
      - creato nelle ultime 48h
      - con foto_principale
      - conteggi like/commenti aggregati in-query (no N+1)
    """
    num_like = func.count(func.distinct(Like.id)).label("num_like")
    num_commenti = func.count(func.distinct(Commento.id)).label("num_commenti")

    stmt = (
        select(
            Post,
            num_like,
            num_commenti,
        )
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
        # engagement = like + commenti (pesi uguali, modificabile)
        .order_by((num_like + num_commenti).desc())
        .limit(MAX_POST)
        # Eager-load autore per evitare N+1 in serializzazione
        .options(selectinload(Post.autore))
    )

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
# QUERY — SFIDE TRENDING (no anteprima partecipanti)
# ============================================================

def _sfide_trending(
    db: Session, cutoff: datetime, ora: datetime
) -> list[dict[str, Any]]:
    """
    Sfide trending:
      - autore pubblico e non bannato
      - visibilita = 'tutti' (solo sfide pubbliche)
      - creata nelle ultime 48h
      - scadenza futura (ancora attiva)
      - NON include foto partecipanti (solo CTA partecipa/scatta)
    """
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
            Sfida.scadenza > ora,             # non scaduta
            Sfida.visibilita == "tutti",      # solo sfide pubbliche
            Utente.is_privato.is_(False),
            Utente.is_banned.is_(False),
        )
        .group_by(Sfida.id)
        .order_by(num_partecipanti.desc(), Sfida.creato_at.desc())
        .limit(MAX_SFIDE)
        .options(selectinload(Sfida.autore))
    )

    rows = db.execute(stmt).all()

    return [
        {
            "id": sfida.id,
            "tipo": "sfida",
            # REGOLA: NO anteprime partecipanti in Esplora
            "media_url": None,
            "testo": sfida.tema,
            "tema": sfida.tema,
            "durata_ore": sfida.durata_ore,
            "scadenza": sfida.scadenza.isoformat(),
            "is_scaduta": False,              # garantito da WHERE
            "is_pubblica": True,              # garantito da WHERE
            "num_partecipanti": n_part,
            "engagement": n_part * 2,
            "creato_at": sfida.creato_at.isoformat(),
            # CTA frontend: mostra "Partecipa / Scatta"
            "azione": "partecipa",
            "autore": _autore_pubblico(sfida.autore),
        }
        for sfida, n_part in rows
    ]


# ============================================================
# QUERY — SONDAGGI TRENDING (esclude votati da `me`)
# ============================================================

def _sondaggi_trending(
    db: Session, cutoff: datetime, ora: datetime, utente_id: int
) -> list[dict[str, Any]]:
    """
    Sondaggi trending:
      - autore pubblico e non bannato
      - creato nelle ultime 48h
      - scadenza futura
      - ESCLUDE sondaggi gia' votati da `utente_id`

    Implementazione:
      - Sub-query: id sondaggi gia' votati dall'utente corrente -> NOT IN.
      - Conteggi voti per opzione: caricati via JSON aggregation o
        parse Python dopo una singola query group-by.
    """
    # ── 1) Sub-query: id sondaggi votati dall'utente ────────────────
    sondaggi_votati_stmt = select(VotoSondaggio.sondaggio_id).where(
        VotoSondaggio.utente_id == utente_id
    )

    # ── 2) Query principale con totale voti aggregato ───────────────
    totale_voti = func.count(VotoSondaggio.id).label("totale_voti")

    stmt = (
        select(Sondaggio, totale_voti)
        .join(Utente, Utente.id == Sondaggio.autore_id)
        .outerjoin(VotoSondaggio, VotoSondaggio.sondaggio_id == Sondaggio.id)
        .where(
            Sondaggio.creato_at >= cutoff,
            Sondaggio.scadenza > ora,                    # non scaduto
            Sondaggio.id.notin_(sondaggi_votati_stmt),   # non votato da me
            Utente.is_privato.is_(False),
            Utente.is_banned.is_(False),
        )
        .group_by(Sondaggio.id)
        .order_by(totale_voti.desc(), Sondaggio.creato_at.desc())
        .limit(MAX_SONDAGGI)
        .options(selectinload(Sondaggio.autore))
    )

    rows = db.execute(stmt).all()
    if not rows:
        return []

    # ── 3) Batch: conteggio voti per opzione in UNA sola query ──────
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
    # Mappa: {sondaggio_id: {opzione_index: conteggio}}
    mappa_conteggi: dict[int, dict[int, int]] = {}
    for sid, opt_idx, cnt in db.execute(conteggi_stmt).all():
        mappa_conteggi.setdefault(sid, {})[opt_idx] = cnt

    # ── 4) Serializzazione ──────────────────────────────────────────
    import json
    risultati = []
    for sondaggio, totale in rows:
        try:
            opzioni = json.loads(sondaggio.opzioni)
        except (json.JSONDecodeError, TypeError):
            continue  # skip sondaggio malformato

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
            "ho_votato": False,              # garantito da WHERE NOT IN
            "mia_opzione": None,
            "scadenza": sondaggio.scadenza.isoformat(),
            "engagement": totale,
            "creato_at": sondaggio.creato_at.isoformat(),
            "autore": _autore_pubblico(sondaggio.autore),
        })
    return risultati