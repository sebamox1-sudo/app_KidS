import json
import os
import firebase_admin
from firebase_admin import credentials, messaging
from sqlalchemy.orm import Session
from app.models.modelli import TokenDispositivoFCM
from app.database import SessionLocal

# Inizializza Firebase Admin una sola volta
_inizializzato = False

def _inizializza():
    global _inizializzato
    if _inizializzato:
        return
    cred_json = os.getenv("FIREBASE_CREDENTIALS")
    if not cred_json:
        return
    cred = credentials.Certificate(json.loads(cred_json))
    firebase_admin.initialize_app(cred)
    _inizializzato = True


def manda_notifica(
    db: Session,
    destinatario_id: int,
    titolo: str,
    corpo: str,
    dati: dict = None,
    tipo: str = "generico",
    extra: dict = None,
):
    try:
        _inizializza()

        token_obj = db.query(TokenDispositivoFCM).filter(
            TokenDispositivoFCM.utente_id == destinatario_id
        ).first()
        if not token_obj:
            return

        # Costruisci il payload data per il deep linking
        # Firebase richiede che tutti i valori siano stringhe
        payload_data = {
            "tipo": tipo,
            "post_id": str(extra.get("post_id", "") if extra else ""),
            "sfida_id": str(extra.get("sfida_id", "") if extra else ""),
            "tema": str(extra.get("tema", "") if extra else ""),
            "mittente_id": str(extra.get("mittente_id", "") if extra else ""),
            "mittente_username": str(extra.get("mittente_username", "") if extra else ""),
            "mittente_nome": str(extra.get("mittente_nome", "") if extra else ""),
            "mittente_foto": str(extra.get("mittente_foto", "") if extra else ""),
        }

        # Merge con dati custom se presenti
        if dati:
            payload_data.update({k: str(v) for k, v in dati.items()})

        message = messaging.Message(
            notification=messaging.Notification(
                title=titolo,
                body=corpo,
            ),
            data=payload_data,
            token=token_obj.token,
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    sound="default",
                    channel_id="high_importance_channel",
                    click_action="FLUTTER_NOTIFICATION_CLICK",
                    default_vibrate_timings=True,
                    default_light_settings=True,
                ),
            ),
            apns=messaging.APNSConfig(
                headers={"apns-priority": "10"},
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        sound="default",
                        badge=1,
                        content_available=True,
                    ),
                ),
            ),
        )

        messaging.send(message)

    except firebase_admin.messaging.UnregisteredError:
        db.query(TokenDispositivoFCM).filter(
            TokenDispositivoFCM.token == token_obj.token
        ).delete()
        db.commit()
    except Exception as e:
        print(f"Errore FCM: {e}")

def manda_notifica_safe(destinatario_id: int, titolo: str, corpo: str, **kwargs):
    db = SessionLocal()
    try:
        manda_notifica(db, destinatario_id, titolo, corpo, **kwargs)
    finally:
        db.close()

def fan_out_follower_fcm(autore_id: int, post_id: int):
    """Manda FCM a tutti i follower dell'autore. 
    Chiamato da BackgroundTasks: apre una sessione DB propria perché 
    quella della request è già chiusa quando il task parte."""
    from app.database import SessionLocal
    from app.models.modelli import Utente
    
    db = SessionLocal()
    try:
        autore = db.query(Utente).filter(Utente.id == autore_id).first()
        if not autore:
            return
        
        follower_ids = [f.follower_id for f in autore.follower_rel]
        for fid in follower_ids:
            try:
                manda_notifica(
                    db, fid,
                    "📸 Nuovo post!",
                    f"{autore.nome} ha pubblicato",
                    tipo="post",
                    extra={
                        "post_id": post_id,
                        "mittente_id": autore_id,
                        "mittente_username": autore.username,
                        "mittente_nome": autore.nome,
                        "mittente_foto": autore.foto_profilo or "",
                    },
                )
            except Exception:
                continue  # non bloccare gli altri destinatari
    finally:
        db.close()