from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.models.modelli import (
    Utente, BadgeUtente, Post, Commento, Voto, Follow,
    Like, PartecipazioneSfida, VotoSfida, Sfida,
)

# ============================================================
# BADGE SERVICE — backend è fonte di verità
# Badge assegnati UNA SOLA VOLTA, permanenti, anti-race condition
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
    "stella":        lambda s: s["media_voti_ricevuta"] is not None and s["media_voti_ricevuta"] >= 8.0,
    "perfetto":      lambda s: s["voto_max_ricevuto"] is not None and s["voto_max_ricevuto"] >= 10.0,
}


async def verifica_badge(
    utente: Utente,
    db: Session,
    nuovo_commento: bool = False,
    voto_negativo: bool = False,
    partecipazione_rapida: bool = False,
    vincita_sfida: bool = False,
) -> list[str]:
    """
    Calcola le statistiche e sblocca i nuovi badge.
    Usa INSERT ... ON CONFLICT DO NOTHING per garantire
    che ogni badge venga assegnato una sola volta anche
    in caso di race condition o chiamate parallele.
    Ritorna la lista dei NUOVI badge sbloccati (stringhe).
    """
    # Query diretta — non usa cache SQLAlchemy
    badge_sbloccati = {
        b.tipo for b in db.query(BadgeUtente).filter(
            BadgeUtente.utente_id == utente.id
        ).all()
    }

    stats = _calcola_statistiche(utente, db)
    stats["partecipazione_rapida"] = partecipazione_rapida

    nuovi = []

    for tipo, condizione in BADGE_CONDIZIONI.items():
        # Skip badge già sbloccati — immutabili
        if tipo in badge_sbloccati:
            continue

        try:
            if not condizione(stats):
                continue

            # INSERT ... ON CONFLICT DO NOTHING — anti race condition
            # Anche se due richieste arrivano contemporaneamente,
            # il DB garantisce che il badge venga inserito una sola volta
            stmt = pg_insert(BadgeUtente).values(
                utente_id=utente.id,
                tipo=tipo,
            ).on_conflict_do_nothing(
                constraint="uq_badge_utente_tipo"
            )
            result = db.execute(stmt)

            # rows_affected == 1 → badge davvero nuovo
            if result.rowcount == 1:
                nuovi.append(tipo)

        except Exception:
            # Non bloccare la pubblicazione per un errore badge
            pass

    if nuovi:
        db.commit()

    return nuovi


def _calcola_statistiche(utente: Utente, db: Session) -> dict:
    uid = utente.id

    # ── Conteggi base con COUNT() ────────────────────────
    num_post = db.query(func.count(Post.id)).filter(
        Post.autore_id == uid
    ).scalar() or 0

    num_commenti = db.query(func.count(Commento.id)).filter(
        Commento.autore_id == uid
    ).scalar() or 0

    num_follower = db.query(func.count(Follow.id)).filter(
        Follow.seguito_id == uid
    ).scalar() or 0

    streak_giorni = utente.streak.giorni if utente.streak else 0

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

    # ── Media e max voti ricevuti (sui post dell'utente) ──
    voti_ricevuti = db.query(
        func.avg(Voto.voto),
        func.max(Voto.voto),
    ).filter(
        Voto.post_id.in_(post_ids)
    ).first()

    media_voti = float(voti_ricevuti[0]) if voti_ricevuti[0] is not None else None
    voto_max = float(voti_ricevuti[1]) if voti_ricevuti[1] is not None else None

    # ── Sfide ────────────────────────────────────────────
    sfide_partecipate = db.query(func.count(PartecipazioneSfida.id)).filter(
        PartecipazioneSfida.utente_id == uid
    ).scalar() or 0

    sfide_vinte = db.query(func.count(Sfida.id)).filter(
        Sfida.vincitore_id == uid
    ).scalar() or 0

    # ── Sfide consecutive — conta le sfide nelle ultime 72h consecutive ──
    # Usa il contatore pre-calcolato sull'utente (aggiornato da sfide.py)
    sfide_consecutive = utente.sfide_partecipate  # TODO: implementare logica consecutiva vera

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
        "sfide_consecutive": sfide_consecutive,
        "sfide_vinte": sfide_vinte,
        "partecipazione_rapida": False,
    }