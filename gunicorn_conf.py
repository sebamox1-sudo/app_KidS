import multiprocessing
import os

# ============================================================
# GUNICORN + UVICORN WORKERS — config production
# ============================================================

# Port binding
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

# ──────────────────────────────────────────────────────────────
# WORKERS
# ──────────────────────────────────────────────────────────────
# Formula classica: (2 × CPU_CORES) + 1
# Ma per UvicornWorker (async I/O) basta CPU_CORES × 2 perché
# ogni worker gestisce centinaia di connessioni concorrenti
# via event loop.
#
# Su Railway Hobby (1 vCPU): 2 workers
# Su Railway Pro (2 vCPU):   4 workers
# Su Railway 4 vCPU:         8 workers
# ──────────────────────────────────────────────────────────────
workers = int(os.getenv("WEB_CONCURRENCY", "2"))
worker_class = "uvicorn.workers.UvicornWorker"

# ──────────────────────────────────────────────────────────────
# TIMEOUTS
# ──────────────────────────────────────────────────────────────
timeout = 60            # hard kill dopo 60s (upload foto può durare)
graceful_timeout = 30   # tempo per finire request aperte al restart
keepalive = 5           # keep-alive HTTP (reduce reconnection overhead)

# ──────────────────────────────────────────────────────────────
# LIFECYCLE
# ──────────────────────────────────────────────────────────────
# Ricicla il worker ogni N richieste → evita memory leak lenti
max_requests = 1000
max_requests_jitter = 100  # randomizza per evitare restart sincroni

# ──────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────
accesslog = "-"    # stdout
errorlog = "-"     # stderr
loglevel = os.getenv("LOG_LEVEL", "info")
access_log_format = '%({x-forwarded-for}i)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s %(D)sµs'

# ──────────────────────────────────────────────────────────────
# PRELOAD
# ──────────────────────────────────────────────────────────────
# preload_app=True: carica l'app UNA VOLTA nel master process,
# poi forka i worker. Riduce uso memoria (shared code pages) e
# startup time.
# ATTENZIONE: con preload le connessioni DB aperte nel master 
# vengono ereditate male dai fork. Tenerlo False per sicurezza,
# a meno che non usi un init hook per resettare il pool.
preload_app = False

# ──────────────────────────────────────────────────────────────
# HOOKS
# ──────────────────────────────────────────────────────────────
def when_ready(server):
    server.log.info(f"🚀 Gunicorn ready — {workers} workers")

def worker_int(worker):
    worker.log.info(f"⚠️  Worker {worker.pid} interrupted")