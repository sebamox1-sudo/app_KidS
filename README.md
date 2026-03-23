# KidS Backend 🚀

Backend Python per l'app social KidS, costruito con FastAPI e PostgreSQL.

## Struttura

```
kids_backend/
├── app/
│   ├── main.py              # Entry point FastAPI
│   ├── database.py          # Connessione PostgreSQL
│   ├── dependencies.py      # JWT auth middleware
│   ├── models/
│   │   └── modelli.py       # Tabelle database (SQLAlchemy)
│   ├── schemas/
│   │   └── schemi.py        # Validazione dati (Pydantic)
│   ├── routers/
│   │   ├── auth.py          # Login, registrazione
│   │   ├── utenti.py        # Profili, follow, ricerca
│   │   └── post.py          # Feed, like, commenti, voti
│   └── services/
│       ├── auth_service.py  # JWT, password hash
│       └── badge_service.py # Logica sblocco badge
├── requirements.txt
├── .env.example
└── README.md
```

## Setup locale

### 1. Installa PostgreSQL
- Mac: `brew install postgresql`
- Windows: scarica da postgresql.org
- Crea il database: `createdb kids_db`

### 2. Crea ambiente virtuale Python
```bash
cd kids_backend
python -m venv venv
source venv/bin/activate  # Mac/Linux
venv\Scripts\activate     # Windows
```

### 3. Installa dipendenze
```bash
pip install -r requirements.txt
```

### 4. Configura variabili d'ambiente
```bash
cp .env.example .env
# Apri .env e modifica DATABASE_URL con i tuoi dati
# Genera SECRET_KEY con: python -c "import secrets; print(secrets.token_hex(32))"
```

### 5. Avvia il server
```bash
uvicorn app.main:app --reload
```

Il server parte su http://localhost:8000
Documentazione API su http://localhost:8000/docs

## API disponibili

### Auth
- `POST /auth/registrati` — Registra nuovo utente
- `POST /auth/login` — Login con email/password
- `GET  /auth/me` — Profilo utente corrente

### Utenti
- `GET    /utenti/{username}` — Profilo utente
- `PATCH  /utenti/me/profilo` — Aggiorna profilo
- `POST   /utenti/me/foto` — Upload foto profilo
- `POST   /utenti/{username}/segui` — Segui utente
- `DELETE /utenti/{username}/segui` — Smetti di seguire
- `GET    /utenti/cerca/{query}` — Ricerca utenti
- `GET    /utenti/{username}/badge` — Badge utente

### Post
- `POST   /post/` — Pubblica post con foto
- `GET    /post/feed` — Feed cronologico
- `POST   /post/{id}/like` — Metti like
- `DELETE /post/{id}/like` — Togli like
- `POST   /post/{id}/vota` — Vota post (anonimo o no)
- `GET    /post/{id}/commenti` — Lista commenti
- `POST   /post/{id}/commenti` — Aggiungi commento
- `DELETE /post/{id}` — Elimina post

## Deploy su Railway (gratuito)

1. Crea account su railway.app
2. Crea nuovo progetto → "Deploy from GitHub"
3. Aggiungi servizio PostgreSQL
4. Imposta variabili d'ambiente in Railway
5. Deploy automatico ad ogni push

## Prossimi step

- [ ] Router sfide flash
- [ ] Router sondaggi
- [ ] Router notifiche
- [ ] Notifiche push con Firebase
- [ ] Google Sign-In
- [ ] Apple Sign-In
