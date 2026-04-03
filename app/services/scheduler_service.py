from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.modelli import Utente, Streak, TokenDispositivoFCM
from app.services.fcm_service import manda_notifica
from datetime import datetime, timezone, timedelta
import random

scheduler = BackgroundScheduler()


def _get_db() -> Session:
    return SessionLocal()


# ============================================================
# JOB 1 — Reminder streak in scadenza (ogni ora)
# Manda notifica a chi non ha postato nelle ultime 20 ore
# ============================================================
def check_streak_in_scadenza():
    db = _get_db()
    try:
        ora = datetime.now(timezone.utc)
        soglia = ora - timedelta(hours=20)  # Chi non posta da 20h è a rischio

        streak_a_rischio = db.query(Streak).filter(
            Streak.giorni > 0,
            Streak.ultimo_post < soglia,
            Streak.ultimo_post > ora - timedelta(hours=24),
        ).all()

        for streak in streak_a_rischio:
            ore_rimanenti = 24 - int(
                (ora - streak.ultimo_post.replace(
                    tzinfo=timezone.utc)).total_seconds() / 3600
            )
            manda_notifica(
                db=db,
                destinatario_id=streak.utente_id,
                titolo="🔥 Il tuo fuoco sta per spegnersi!",
                corpo=f"Hai {ore_rimanenti}h per postare e mantenere la streak di {streak.giorni} giorni!",
            )
    except Exception as e:
        print(f"Errore check_streak: {e}")
    finally:
        db.close()


# ============================================================
# JOB 2 — Reminder post giornaliero (ogni giorno alle 12:00)
# Manda notifica random a utenti che non hanno postato oggi
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

        # Prendi utenti che NON hanno postato oggi
        # e hanno un token FCM registrato
        utenti_con_token = db.query(Utente).join(
            TokenDispositivoFCM,
            TokenDispositivoFCM.utente_id == Utente.id
        ).all()

        for utente in utenti_con_token:
            streak = utente.streak
            if streak and streak.ultimo_post:
                ultimo = streak.ultimo_post.replace(
                    tzinfo=timezone.utc).date()
                if ultimo == oggi:
                    continue  # Ha già postato oggi, skip

            titolo, corpo = random.choice(MESSAGGI_REMINDER)
            manda_notifica(
                db=db,
                destinatario_id=utente.id,
                titolo=titolo,
                corpo=corpo,
            )
    except Exception as e:
        print(f"Errore reminder: {e}")
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

    # Reminder giornaliero alle 12:00 UTC (14:00 ora italiana)
    scheduler.add_job(
        reminder_post_giornaliero,
        CronTrigger(hour=12, minute=0),
        id="reminder_giornaliero",
        replace_existing=True,
    )

    scheduler.start()
    print("✅ Scheduler avviato!")