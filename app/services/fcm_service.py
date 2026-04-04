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
            
            # ✨ CONFIGURAZIONE ANDROID PREMIUM
            android=messaging.AndroidConfig(
                priority="high", # Obbligatorio per accendere lo schermo
                notification=messaging.AndroidNotification(
                    sound="default",
                    channel_id="high_importance_channel", # ✨ Cambiato nome (vedi punto 2)
                    click_action="FLUTTER_NOTIFICATION_CLICK",
                    default_vibrate_timings=True,
                    default_light_settings=True,
                ),
            ),
            
            # ✨ CONFIGURAZIONE iOS PREMIUM (Mancava!)
            apns=messaging.APNSConfig(
                headers={
                    "apns-priority": "10", # 10 = Massima priorità, accende lo schermo su iOS
                },
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        sound="default",
                        badge=1,
                        content_available=True, # Risveglia l'app in background
                    ),
                ),
            ),
        )

        messaging.send(message)

    except firebase_admin.messaging.UnregisteredError:
        print(f"=== FCM: Token scaduto o app disinstallata per utente {destinatario_id}. Rimuovo il token.")
        db.query(TokenDispositivoFCM).filter(TokenDispositivoFCM.token == token_obj.token).delete()
        db.commit()
    except Exception as e:
        print(f"Errore FCM: {e}")