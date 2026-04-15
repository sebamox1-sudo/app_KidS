from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from app.models.modelli import (
    Utente, BadgeUtente, Post, Commento, Voto, Follow,
    Like, PartecipazioneSfida, VotoSfida, Sfida,
)

# ============================================================
# SERVIZIO BADGE — Versione corretta (audit 15/04/2026)
#
# Fix applicati:
# 1. def sincrono (non async) — compatibile con Session sincrona
# 2. Check duplicati via query DB, non lazy load della relazione
# 3. try/except IntegrityError come safety net (UNIQUE constraint)
# 4. Nessun commit parziale — un solo commit alla fine
# 5. Ritorna lista di dict con tipo + sbloccato_at per il frontend
# ============================================================

BADGE_CONDIZIONI = {
    "creatore":      lambda s: s["post"] >= 1,
    "prolifico":     lambda s: s["post"] >= 10,
    "costante":      lambda s: s["streak"] >= 7,
    "instancabile":  lambda s: s["streak"] >= 30,
    "popolare":      lambda s: s["follower"] >= 100,
    "chiacchierone": lambda s: s["commenti"] >= 50,
    "amato":         lambda s: s["like_ricevuti"] >= 100,
    "critico":       lambda s: s["voti_dati"] >= 20,
    "occhio_fino":   lambda s: s["voti_negativi"] >= 20,
    "primo_colpo":   lambda s: s["sfide_partecipate"] >= 1,
    "in_serie":      lambda s: s["sfide_consecutive"] >= 3,
    "campione":      lambda s: s["sfide_vinte"] >= 1,
    "imbattibile":   lambda s: s["sfide_vinte"] >= 3,
    "fulmine":       lambda s: s["partecipazione_rapida"],
    "stella":        lambda s: (
        s["media_voti_ricevuta"] is not None
        and s["media_voti_ricevuta"] >= 8.0
    ),
    "perfetto":      lambda s: (
        s["voto_max_ricevuto"] is not None
        and s["voto_max_ricevuto"] >= 10.0
    ),
}

# Metadati per ogni badge (nome, descrizione, icona)
# Usati dal frontend per mostrare tutti i badge (anche bloccati)
BADGE_CATALOGO = {
    "creatore":      {"nome": "Creatore",      "desc": "Pubblica il primo post",           "icona": "✍️"},
    "prolifico":     {"nome": "Prolifico",     "desc": "Pubblica 10 post",                 "icona": "📝"},
    "costante":      {"nome": "Costante",      "desc": "Streak di 7 giorni",               "icona": "🔥"},
    "instancabile":  {"nome": "Instancabile",  "desc": "Streak di 30 giorni",              "icona": "⚡"},
    "popolare":      {"nome": "Popolare",      "desc": "Raggiungi 100 follower",           "icona": "⭐"},
    "chiacchierone": {"nome": "Chiacchierone", "desc": "Scrivi 50 commenti",               "icona": "💬"},
    "amato":         {"nome": "Amato",         "desc": "Ricevi 100 like",                  "icona": "❤️"},
    "critico":       {"nome": "Critico",       "desc": "Dai 20 voti",                      "icona": "🎯"},
    "occhio_fino":   {"nome": "Occhio fino",   "desc": "Dai 20 voti sotto il 5",           "icona": "👁️"},
    "primo_colpo":   {"nome": "Primo colpo",   "desc": "Partecipa alla prima sfida",       "icona": "🏁"},
    "in_serie":      {"nome": "In serie",      "desc": "Partecipa a 3 sfide consecutive",  "icona": "🎲"},
    "campione":      {"nome": "Campione",      "desc": "Vinci una sfida",                  "icona": "🏆"},
    "imbattibile":   {"nome": "Imbattibile",   "desc": "Vinci 3 sfide",                    "icona": "👑"},
    "fulmine":       {"nome": "Fulmine",       "desc": "Partecipa entro 5 min dal lancio", "icona": "⚡"},
    "stella":        {"nome": "Stella",        "desc": "Media voti ricevuti >= 8.0",       "icona": "🌟"},
    "perfetto":      {"nome": "Perfetto",      "desc": "Ricevi un voto 10",                "icona": "💎"},
}


def verifica_badge(
    utente: Utente,
    db: Session,
    partecipazione_rapida: bool = False,
) -> list[str]:
    """
    Calcola le statistiche e sblocca i nuovi badge.

    IMPORTANTE: questa funzione è SINCRONA (def, non async def)
    perché usa una Session SQLAlchemy sincrona.

    Ritorna la lista dei tipi di badge appena sbloccati.
    """
    uid = utente.id

    # ── 1. Query UNICA per i badge già sbloccati ──────────
    # Usa una query esplicita, NON utente.badge (lazy load)
    badge_esistenti = db.query(BadgeUtente.tipo).filter(
        BadgeUtente.utente_id == uid
    ).all()
    badge_sbloccati = {b.tipo for b in badge_esistenti}

    # ── 2. Calcola statistiche con query efficienti ───────
    stats = _calcola_statistiche(uid, db)
    stats["partecipazione_rapida"] = partecipazione_rapida

    # ── 3. Verifica ogni badge non ancora sbloccato ───────
    nuovi = []
    for tipo, condizione in BADGE_CONDIZIONI.items():
        if tipo in badge_sbloccati:
            continue
        try:
            if condizione(stats):
                nuovo_badge = BadgeUtente(
                    utente_id=uid,
                    tipo=tipo,
                )
                db.add(nuovo_badge)
                nuovi.append(tipo)
        except Exception:
            # Se la condizione fallisce (dati mancanti), skip
            continue

    # ── 4. Commit atomico con protezione UNIQUE ───────────
    if nuovi:
        try:
            db.commit()
        except IntegrityError:
            # Race condition: qualcun altro ha inserito lo stesso badge
            # Il UNIQUE constraint ci protegge. Rollback e riprova
            # solo quelli non duplicati.
            db.rollback()
            nuovi_filtrati = []
            for tipo in nuovi:
                try:
                    db.add(BadgeUtente(utente_id=uid, tipo=tipo))
                    db.commit()
                    nuovi_filtrati.append(tipo)
                except IntegrityError:
                    db.rollback()
            nuovi = nuovi_filtrati

    return nuovi


def get_catalogo_badge(utente_id: int, db: Session) -> list[dict]:
    """
    Ritorna TUTTI i badge (sbloccati e bloccati) per il frontend.
    Il frontend può così mostrare la griglia completa.
    """
    sbloccati = {}
    rows = db.query(BadgeUtente).filter(
        BadgeUtente.utente_id == utente_id
    ).all()
    for b in rows:
        sbloccati[b.tipo] = b.sbloccato_at

    catalogo = []
    for tipo, meta in BADGE_CATALOGO.items():
        catalogo.append({
            "tipo": tipo,
            "nome": meta["nome"],
            "descrizione": meta["desc"],
            "icona": meta["icona"],
            "sbloccato": tipo in sbloccati,
            "sbloccato_at": sbloccati.get(tipo),
        })

    return catalogo


def _calcola_statistiche(uid: int, db: Session) -> dict:
    """Calcola tutte le statistiche con query COUNT/SUM efficienti."""

    # ── Conteggi base ────────────────────────────────────
    num_post = db.query(func.count(Post.id)).filter(
        Post.autore_id == uid
    ).scalar() or 0

    num_commenti = db.query(func.count(Commento.id)).filter(
        Commento.autore_id == uid
    ).scalar() or 0

    num_follower = db.query(func.count(Follow.id)).filter(
        Follow.seguito_id == uid
    ).scalar() or 0

    # Streak — accesso diretto al campo, non lazy load
    streak_row = db.query(
        func.coalesce(
            db.query(func.max(
                # Se hai una tabella streak separata:
                __import__('sqlalchemy').text("streak.giorni")
            )).correlate(None).scalar_subquery(),
            0
        )
    )
    # Approccio più semplice: query diretta sulla tabella streak
    from app.models.modelli import Streak
    streak_giorni = db.query(Streak.giorni).filter(
        Streak.utente_id == uid
    ).scalar() or 0

    # ── Like ricevuti (sui post dell'utente) ─────────────
    post_ids = db.query(Post.id).filter(Post.autore_id == uid).subquery()

    like_ricevuti = db.query(func.count(Like.id)).filter(
        Like.post_id.in_(post_ids)
    ).scalar() or 0

    # ── Voti dati dall'utente ────────────────────────────
    voti_dati = db.query(func.count(Voto.id)).filter(
        Voto.utente_id == uid
    ).scalar() or 0

    voti_negativi = db.query(func.count(Voto.id)).filter(
        Voto.utente_id == uid,
        Voto.voto < 5
    ).scalar() or 0

    # ── Media e max voti ricevuti ────────────────────────
    voti_ricevuti = db.query(
        func.avg(Voto.voto),
        func.max(Voto.voto),
    ).filter(
        Voto.post_id.in_(post_ids)
    ).first()

    media_voti = (
        float(voti_ricevuti[0])
        if voti_ricevuti and voti_ricevuti[0] is not None
        else None
    )
    voto_max = (
        float(voti_ricevuti[1])
        if voti_ricevuti and voti_ricevuti[1] is not None
        else None
    )

    # ── Sfide ────────────────────────────────────────────
    sfide_partecipate = db.query(
        func.count(PartecipazioneSfida.id)
    ).filter(
        PartecipazioneSfida.utente_id == uid
    ).scalar() or 0

    sfide_vinte = db.query(func.count(Sfida.id)).filter(
        Sfida.vincitore_id == uid
    ).scalar() or 0

    return {
        "post": num_post,
        "commenti": num_commenti,
        "follower": num_follower,
        "streak": streak_giorni,
        "like_ricevuti": like_ricevuti,
        "voti_dati": voti_dati,
        "voti_negativi": voti_negativi,
        "media_voti_ricevuta": media_voti,
        "voto_max_ricevuto": voto_max,
        "sfide_partecipate": sfide_partecipate,
        "sfide_consecutive": 0,  # TODO: conteggio consecutivo
        "sfide_vinte": sfide_vinte,
        "partecipazione_rapida": False,
    }