from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.models import modelli
from app.routers import auth, utenti, post, notifiche, sondaggi, sfide, classifica
import os
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from app.database import engine, Base
from app.services.scheduler_service import avvia_scheduler
from app.routers import esplora
from contextlib import asynccontextmanager
from app.routers import ws_commenti
from app.routers import blocco_segnalazioni
from app.routers import ws_sfide

modelli.Base.metadata.create_all(bind=engine)

os.makedirs("uploads/post", exist_ok=True)
os.makedirs("uploads/profili", exist_ok=True)
os.makedirs("uploads/sfide", exist_ok=True)

limiter = Limiter(key_func=get_remote_address)

# ← lifespan gestisce avvio e spegnimento
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Qui puoi aggiungere cleanup se serve

app = FastAPI(
    title="KidS API",
    description="Backend per l'app social KidS",
    version="1.0.0",
    lifespan=lifespan,  # ← collegato qui
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,   # False perché con "*" i browser lo rifiutano comunque
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

app.include_router(auth.router)
app.include_router(utenti.router)
app.include_router(post.router)
app.include_router(notifiche.router)
app.include_router(sondaggi.router)
app.include_router(sfide.router)
app.include_router(classifica.router)
app.include_router(esplora.router)
app.include_router(ws_commenti.router)
app.include_router(blocco_segnalazioni.router)
app.include_router(ws_sfide.router)

@app.get("/")
def root():
    return {"app": "KidS API", "versione": "1.0.0", "stato": "✅ Online"}

@app.get("/health")
def health():
    return {"stato": "ok"}