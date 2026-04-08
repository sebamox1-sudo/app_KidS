from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Dict, Set
import json
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Registro connessioni: {post_id: set di websocket} ────────
# IMPROVED: Set invece di List — O(1) per rimozione, zero duplicati
_connessioni: Dict[int, Set[WebSocket]] = {}

# IMPROVED: limite connessioni per post — previene abuse
_MAX_CONNESSIONI_PER_POST = 100


def _connetti(post_id: int, ws: WebSocket) -> bool:
    """
    Aggiunge ws al set del post.
    Ritorna False se il limite è stato raggiunto.
    IMPROVED: sincrono (non serviva async), con limite e check duplicati.
    """
    if post_id not in _connessioni:
        _connessioni[post_id] = set()

    # IMPROVED: limite connessioni per post
    if len(_connessioni[post_id]) >= _MAX_CONNESSIONI_PER_POST:
        logger.warning(f"Limite connessioni raggiunto per post {post_id}")
        return False

    _connessioni[post_id].add(ws)
    logger.debug(f"WS connesso: post={post_id} totale={len(_connessioni[post_id])}")
    return True


def _disconnetti(post_id: int, ws: WebSocket):
    """
    Rimuove ws dal set. Pulisce il dizionario se il set è vuoto.
    IMPROVED: discard non lancia eccezione se ws non è presente.
    """
    if post_id in _connessioni:
        _connessioni[post_id].discard(ws)  # IMPROVED: discard vs remove
        if not _connessioni[post_id]:
            del _connessioni[post_id]
        logger.debug(f"WS disconnesso: post={post_id}")


async def broadcast_commento(post_id: int, payload: dict):
    """
    Invia payload a tutti i client connessi al post.
    IMPROVED: itera su una copia del set — sicuro anche se il set
    viene modificato durante l'iterazione.
    """
    if post_id not in _connessioni:
        return

    # IMPROVED: copia del set prima di iterare — zero race condition
    connessioni_attive = set(_connessioni[post_id])
    if not connessioni_attive:
        return

    # IMPROVED: serializza una volta sola fuori dal loop
    testo = json.dumps(payload, ensure_ascii=False)
    da_rimuovere = set()

    for ws in connessioni_attive:
        try:
            await ws.send_text(testo)
        except Exception:
            da_rimuovere.add(ws)

    # IMPROVED: rimuovi connessioni morte dopo l'iterazione
    for ws in da_rimuovere:
        _disconnetti(post_id, ws)

    if da_rimuovere:
        logger.debug(f"Rimosse {len(da_rimuovere)} connessioni morte per post {post_id}")


@router.websocket("/ws/comments/{post_id}")
async def ws_commenti(
    post_id: int,
    websocket: WebSocket,
    # IMPROVED: token opzionale pronto per auth futura
    # es: wss://server/ws/comments/123?token=abc
    token: str = Query(default=None),
):
    """
    Endpoint WebSocket commenti real-time.
    Il client si connette all'apertura del pannello.
    NON usare per inviare commenti — usa REST.
    """
    # IMPROVED: struttura pronta per autenticazione
    # Decommentare per proteggere l'endpoint:
    # if token is None or not _valida_token(token):
    #     await websocket.close(code=1008)
    #     return

    await websocket.accept()

    # IMPROVED: controlla limite prima di procedere
    if not _connetti(post_id, websocket):
        await websocket.close(code=1013)  # Try Again Later
        return

    try:
        # IMPROVED: receive_bytes gestisce sia testo che binario
        # senza crashare su dati inattesi dal client
        while True:
            await websocket.receive_bytes()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Errore WS post={post_id}: {e}")
    finally:
        # IMPROVED: cleanup garantito anche in caso di eccezione
        _disconnetti(post_id, websocket)