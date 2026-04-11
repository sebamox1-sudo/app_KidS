from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from sqlalchemy.orm import Session, joinedload
from app.database import SessionLocal
from app.models.modelli import Utente, Streak, TokenDispositivoFCM, RefreshToken
from app.services.fcm_service import manda_notifica
from datetime import datetime, timezone, timedelta
import random

scheduler = BackgroundScheduler()

def _get_db() -> Session:
    return SessionLocal()


# ============================================================
# JOB 1 — Reminder streak in scadenza (ogni ora)
# ============================================================
def check_streak_in_scadenza():
    db = _get_db()
    try:
        ora = datetime.now(timezone.utc)
        
        # ✨ FIX 1: Finestra esatta di 1 ora.
        # Così se il job gira ogni ora, un utente viene pescato UNA SOLA VOLTA (esattamente alla 20esima ora di inattività)
        limite_massimo = ora - timedelta(hours=20) 
        limite_minimo = ora - timedelta(hours=21)

        streak_a_rischio = db.query(Streak).filter(
            Streak.giorni > 0,
            Streak.ultimo_post <= limite_massimo,
            Streak.ultimo_post > limite_minimo,
        ).all()

        for streak in streak_a_rischio:
            # Calcolo le ore rimanenti reali
            ore_trascorse = (ora - streak.ultimo_post.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            ore_rimanenti = max(1, int(24 - ore_trascorse))

            manda_notifica(
                db=db,
                destinatario_id=streak.utente_id,
                titolo="🔥 Il tuo fuoco sta per spegnersi!",
                corpo=f"Hai {ore_rimanenti}h per postare e mantenere la streak di {streak.giorni} giorni!",
            )
            print(f"⏰ Inviata notifica salvataggio streak a utente {streak.utente_id}")
            
    except Exception as e:
        print(f"Errore check_streak: {e}")
    finally:
        db.close()


# ============================================================
# JOB 2 — Reminder post giornaliero (ogni giorno)
# ============================================================
MESSAGGI_REMINDER = [
    ("📸 Cosa stai combinando?", "Condividi un momento della tua giornata!"),
    ("👀 I tuoi amici ti stanno aspettando!", "Posta qualcosa oggi!"),
    ("🚀 Mantieni viva la community!", "Hai qualcosa da condividere oggi?"),
    ("⚡ Nuova sfida disponibile!", "Entra e partecipa prima che scada!"),
    ("🌟 È ora di brillare!", "Condividi qualcosa di speciale oggi!"),
]

def reminder_post_giornaliero():
    db = _get_db()
    try:
        ora = datetime.now(timezone.utc)
        oggi = ora.date()

        # ✨ FIX 2: Usiamo joinedload(Utente.streak) per fare UNA SINGOLA QUERY massiva!
        # Questo salva il database da crash istantanei.
        utenti_con_token = db.query(Utente).options(
            joinedload(Utente.streak)
        ).join(
            TokenDispositivoFCM,
            TokenDispositivoFCM.utente_id == Utente.id
        ).all()

        inviate = 0
        for utente in utenti_con_token:
            streak = utente.streak
            if streak and streak.ultimo_post:
                ultimo = streak.ultimo_post.replace(tzinfo=timezone.utc).date()
                if ultimo == oggi:
                    continue  # Ha già postato oggi, lo saltiamo in silenzio

            titolo, corpo = random.choice(MESSAGGI_REMINDER)
            manda_notifica(
                db=db,
                destinatario_id=utente.id,
                titolo=titolo,
                corpo=corpo,
            )
            inviate += 1
            
        print(f"🌞 Inviati {inviate} reminder giornalieri.")
            
    except Exception as e:
        print(f"Errore reminder: {e}")
    finally:
        db.close()

# ============================================================
# JOB 3 — Azzera streak scadute (ogni ora)
# ============================================================
def azzera_streak_scadute():
    db = _get_db()
    try:
        limite = datetime.now(timezone.utc) - timedelta(hours=48)

        scadute = db.query(Streak).filter(
            Streak.giorni > 0,
            Streak.ultimo_post <= limite,
        ).all()

        for streak in scadute:
            streak.giorni = 0

        if scadute:
            db.commit()
            print(f"🧹 Azzerate {len(scadute)} streak scadute.")
    except Exception as e:
        print(f"Errore azzera_streak: {e}")
    finally:
        db.close()


# JOB 4 — Pulizia refresh token scaduti/revocati (ogni giorno)
def pulisci_refresh_token():
    db = _get_db()
    try:
        limite = datetime.now(timezone.utc) - timedelta(days=7)
        cancellati = db.query(RefreshToken).filter(
            (RefreshToken.revocato == True) | (RefreshToken.scadenza < limite)
        ).delete()
        db.commit()
        if cancellati:
            print(f"🧹 Rimossi {cancellati} refresh token scaduti/revocati.")
    except Exception as e:
        print(f"Errore pulizia token: {e}")
    finally:
        db.close()



# ============================================================
# AVVIO SCHEDULER
# ============================================================
def avvia_scheduler():
    # Streak check ogni ora
    scheduler.add_job(
        check_streak_in_scadenza,
        CronTrigger(minute=0),  # ogni ora in punto
        id="streak_check",
        replace_existing=True,
    )

    # Reminder giornaliero alle 12:00 UTC (14:00 in Italia durante l'ora legale CEST)
    scheduler.add_job(
        reminder_post_giornaliero,
        CronTrigger(hour=12, minute=0),
        id="reminder_giornaliero",
        replace_existing=True,
    )

    # Pulizia streak scadute ogni ora (al minuto 30, sfalsato dal check streak)
    scheduler.add_job(
        azzera_streak_scadute,
        CronTrigger(minute=30),
        id="azzera_streak",
        replace_existing=True,
    )

    scheduler.add_job(
        pulisci_refresh_token,
        CronTrigger(hour=3, minute=0),  # alle 3 di notte
        id="pulizia_token",
        replace_existing=True,
    )

    scheduler.start()
    print("✅ Scheduler avviato!")