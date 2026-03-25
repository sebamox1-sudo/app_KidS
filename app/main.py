from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.database import engine, Base
from app.routers import auth, utenti, post, notifiche, sondaggi, sfide, classifica
import os

# --- IMPORT PER IL RATE LIMITING ---
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# ----------------------------------------------------
Base.metadata.create_all(bind=engine)

os.makedirs("uploads/post", exist_ok=True)
os.makedirs("uploads/profili", exist_ok=True)
os.makedirs("uploads/sfide", exist_ok=True)

# 1. Inizializza il Limiter
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="KidS API",
    description="Backend per l'app social KidS",
    version="1.0.0",
)

# 2. Collega il Limiter all'app e gestisci l'errore (429 Too Many Requests)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
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

@app.get("/")
def root():
    return {"app": "KidS API", "versione": "1.0.0", "stato": "✅ Online"}

@app.get("/health")
def health():
    return {"stato": "ok"}