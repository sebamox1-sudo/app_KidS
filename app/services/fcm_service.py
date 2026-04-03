import json
import os
import firebase_admin
from firebase_admin import credentials, messaging
from sqlalchemy.orm import Session
from app.models.modelli import TokenDispositivoFCM

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
):
    """Manda una push notification a un utente specifico."""
    try:
        _inizializza()

        print(f"=== FCM: mando a utente {destinatario_id}: {titolo}")

        # Trova il token FCM del destinatario
        token_obj = db.query(TokenDispositivoFCM).filter(
            TokenDispositivoFCM.utente_id == destinatario_id
        ).first()

        if not token_obj:
            print(f"=== FCM: nessun token per utente {destinatario_id}")
            return  # Utente non ha registrato il token

        print(f"=== FCM: token trovato: {token_obj.token[:20]}...")

        message = messaging.Message(
            notification=messaging.Notification(
                title=titolo,
                body=corpo,
            ),
            data=dati or {},
            token=token_obj.token,
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    sound="default",
                    channel_id="default_channel",
                    click_action="FLUTTER_NOTIFICATION_CLICK",
                ),
            ),
        )

        messaging.send(message)

    except Exception as e:
        print(f"Errore FCM: {e}")
        # Non blocchiamo mai il flusso principale per un errore di notifica