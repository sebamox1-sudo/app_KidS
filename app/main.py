from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.database import engine, Base
from app.routers import auth, utenti, post, notifiche, sondaggi, sfide, classifica
import os

Base.metadata.create_all(bind=engine)

os.makedirs("uploads/post", exist_ok=True)
os.makedirs("uploads/profili", exist_ok=True)
os.makedirs("uploads/sfide", exist_ok=True)

app = FastAPI(
    title="KidS API",
    description="Backend per l'app social KidS",
    version="1.0.0",
)

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