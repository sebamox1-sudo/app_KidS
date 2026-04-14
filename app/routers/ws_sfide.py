from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, Set
import json
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

# {sfida_id: set di websocket connessi}
_connessioni: Dict[int, Set[WebSocket]] = {}
_MAX_CONNESSIONI_PER_SFIDA = 200


def _connetti(sfida_id: int, ws: WebSocket) -> bool:
    if sfida_id not in _connessioni:
        _connessioni[sfida_id] = set()
    if len(_connessioni[sfida_id]) >= _MAX_CONNESSIONI_PER_SFIDA:
        return False
    _connessioni[sfida_id].add(ws)
    return True


def _disconnetti(sfida_id: int, ws: WebSocket):
    if sfida_id in _connessioni:
        _connessioni[sfida_id].discard(ws)
        if not _connessioni[sfida_id]:
            del _connessioni[sfida_id]


async def broadcast_voto(
    sfida_id: int,
    partecipazione_id: int,
    nuova_media: float,
    num_voti:int,
):
    """
    Invia il nuovo voto a tutti i client connessi alla sfida.
    Chiamato da vota_partecipazione dopo il commit.
    """
    if sfida_id not in _connessioni:
        return

    payload = json.dumps({
        "type": "new_vote",
        "partecipazione_id": partecipazione_id,
        "media_voti": nuova_media,
        "num_voti": num_voti,
    })

    da_rimuovere = set()
    for ws in set(_connessioni[sfida_id]):
        try:
            await ws.send_text(payload)
        except Exception:
            da_rimuovere.add(ws)

    for ws in da_rimuovere:
        _disconnetti(sfida_id, ws)


@router.websocket("/ws/sfide/{sfida_id}")
async def ws_sfida(sfida_id: int, websocket: WebSocket):
    """
    Client si connette quando apre la schermata di una sfida.
    Riceve aggiornamenti voti in tempo reale.
    """
    await websocket.accept()

    if not _connetti(sfida_id, websocket):
        await websocket.close(code=1013)
        return

    try:
        while True:
            await websocket.receive_bytes()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Errore WS sfida={sfida_id}: {e}")
    finally:
        _disconnetti(sfida_id, websocket)