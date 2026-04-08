from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, List
import json

router = APIRouter()

# ── Registro connessioni: {post_id: [ws1, ws2, ...]} ─────────
# NEW: struttura in memoria, si svuota al riavvio del server
_connessioni: Dict[int, List[WebSocket]] = {}


# NEW: aggiunge ws alla lista del post
async def _connetti(post_id: int, ws: WebSocket):
    if post_id not in _connessioni:
        _connessioni[post_id] = []
    _connessioni[post_id].append(ws)


# NEW: rimuove ws dalla lista del post
def _disconnetti(post_id: int, ws: WebSocket):
    if post_id in _connessioni:
        _connessioni[post_id] = [
            c for c in _connessioni[post_id] if c != ws
        ]
        if not _connessioni[post_id]:
            del _connessioni[post_id]


# NEW: broadcast a tutti i client connessi a quel post
# Rimuove automaticamente le connessioni chiuse
async def broadcast_commento(post_id: int, payload: dict):
    if post_id not in _connessioni:
        return

    da_rimuovere = []
    for ws in _connessioni[post_id]:
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            # Connessione chiusa — segna per rimozione
            da_rimuovere.append(ws)

    for ws in da_rimuovere:
        _disconnetti(post_id, ws)


# NEW: endpoint WebSocket
@router.websocket("/ws/comments/{post_id}")
async def ws_commenti(post_id: int, websocket: WebSocket):
    await websocket.accept()
    await _connetti(post_id, websocket)
    try:
        # Tiene la connessione aperta — aspetta messaggi
        # (il client non manda nulla via WS, usa solo REST)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _disconnetti(post_id, websocket)