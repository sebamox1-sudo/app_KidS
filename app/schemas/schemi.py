from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, List
from datetime import datetime
import re

# ============================================================
# AUTH
# ============================================================
class RegistrazioneRequest(BaseModel):
    nome: str
    username: str
    email: EmailStr
    password: str

    @field_validator("username")
    @classmethod
    def username_valido(cls, v):
        v = v.strip().lstrip("@").lower()
        if len(v) < 3:
            raise ValueError("Username troppo corto (min 3 caratteri)")
        if len(v) > 30:
            raise ValueError("Username troppo lungo (max 30 caratteri)")
        if not re.match(r'^[a-z0-9_.]+$', v):
            raise ValueError("Username può contenere solo lettere, numeri, _ e .")
        return v

    @field_validator("password")
    @classmethod
    def password_valida(cls, v):
        if len(v) < 8:
            raise ValueError("La password deve avere almeno 8 caratteri")
        # FIX H-5: bcrypt tronca silenziosamente a 72 byte.
        # Senza questo check, "password123A" e "password123A<+64 caratteri>"
        # risultano identiche dopo l'hash → bypass autenticazione.
        if len(v) > 72:
            raise ValueError("La password non può superare 72 caratteri")
        if not any(char.isdigit() for char in v):
            raise ValueError("La password deve contenere almeno un numero")
        if not any(char.isupper() for char in v):
            raise ValueError("La password deve contenere almeno una maiuscola")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    utente: "UtenteResponse"


# ============================================================
# UTENTE — DUE SCHEMA SEPARATI (fix C-4)
# ============================================================

class UtentePublicResponse(BaseModel):
    """
    Schema PUBBLICO — restituito quando altri utenti vedono un profilo.
    NON contiene email o dati interni sensibili.
    Usato in: GET /utenti/{username}, autore nei post/commenti/sfide/sondaggi,
              mittente nelle notifiche, liste follower/seguiti/amici altrui.
    """
    id: int
    nome: str
    username: str
    bio: str
    foto_profilo: Optional[str]
    is_privato: bool
    num_follower: int
    num_seguiti: int
    num_post: int = 0
    streak_giorni: int
    posizione_classifica: int = 0
    streak_ultimo_post: Optional[datetime] = None
    num_amici: int = 0
    ultimi_post: List[dict] = []
    badge_sbloccati: List[str] = []
    model_config = {"from_attributes": True}


class UtenteResponse(BaseModel):
    """
    Schema PRIVATO — restituito SOLO all'utente autenticato per sé stesso.
    Contiene email e dati interni. Usato in: GET /auth/me, POST /auth/login,
    POST /auth/registrati, PATCH /utenti/me/profilo, POST /utenti/me/foto.
    """
    id: int
    nome: str
    username: str
    email: str                      # ← solo per l'utente stesso
    bio: str
    foto_profilo: Optional[str]
    is_privato: bool
    num_follower: int
    num_seguiti: int
    num_post: int = 0
    streak_giorni: int
    onboarding_completato: bool     # ← dato interno
    creato_at: datetime
    sfide_partecipate: int = 0
    sfide_vinte: int = 0
    voti_dati: int = 0
    commenti_scritti: int = 0
    like_ricevuti: int = 0
    posizione_classifica: int = 0
    streak_ultimo_post: Optional[datetime] = None
    num_amici: int = 0
    model_config = {"from_attributes": True}
    ultimi_post: List[dict] = []
    badge_sbloccati: List[str] = []


class AggiornaProfilo(BaseModel):
    nome: Optional[str] = None
    username: Optional[str] = None
    bio: Optional[str] = None
    is_privato: Optional[bool] = None
    onboarding_completato: Optional[bool] = None


# ============================================================
# POST
# ============================================================
class PostResponse(BaseModel):
    id: int
    autore: UtentePublicResponse    # ← pubblico: niente email dell'autore
    foto_principale: Optional[str] = None
    foto_selfie: Optional[str] = None
    testo: Optional[str] = None
    hashtag: str = ""
    num_like: int = 0
    media_voti: Optional[float] = None
    ho_messo_like: bool = False
    ho_votato: bool = False
    mio_voto: Optional[float] = None
    creato_at: datetime

    model_config = {"from_attributes": True}


class VotoPostRequest(BaseModel):
    voto: float

    @field_validator("voto")
    @classmethod
    def voto_valido(cls, v):
        if not 0 <= v <= 10:
            raise ValueError("Il voto deve essere tra 0 e 10")
        return round(v, 1)


# ============================================================
# COMMENTO
# ============================================================
class CommentoRequest(BaseModel):
    testo: str
    risposta_a_id: Optional[int] = None

    @field_validator("testo")
    @classmethod
    def testo_valido(cls, v):
        if len(v.strip()) == 0:
            raise ValueError("Il commento non può essere vuoto")
        if len(v) > 500:
            raise ValueError("Commento troppo lungo (max 500 caratteri)")
        return v.strip()


class CommentoResponse(BaseModel):
    id: int
    autore: UtentePublicResponse    # ← pubblico
    testo: str
    risposta_a_id: Optional[int]
    risposte: List["CommentoResponse"] = []
    creato_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
# SONDAGGIO — con durata variabile e scadenza
# ============================================================
class SondaggioRequest(BaseModel):
    domanda: str
    opzioni: List[str]
    durata_ore: int = 24  # default 24h

    @field_validator("opzioni")
    @classmethod
    def opzioni_valide(cls, v):
        if len(v) < 2:
            raise ValueError("Servono almeno 2 opzioni")
        if len(v) > 4:
            raise ValueError("Massimo 4 opzioni")
        return [o.strip() for o in v if o.strip()]

    @field_validator("durata_ore")
    @classmethod
    def durata_valida(cls, v):
        if v not in [1, 6, 12, 24]:
            raise ValueError("Durata deve essere 1, 6, 12 o 24 ore")
        return v


class VotoSondaggioRequest(BaseModel):
    opzione_index: int
    anonimo: bool = False


class SondaggioResponse(BaseModel):
    id: int
    autore: UtentePublicResponse    # ← pubblico
    domanda: str
    opzioni: List[str]
    voti_per_opzione: List[int]
    totale_voti: int
    ho_votato: bool = False
    mia_opzione: Optional[int] = None
    scadenza: datetime
    creato_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
# SFIDA — con visibilità e lista amici invitati
# ============================================================
class SfidaRequest(BaseModel):
    tema: str
    durata_ore: int
    visibilita: str = "tutti"
    amici_invitati: List[str] = []
    amici_usernames: Optional[List[str]] = None

    @field_validator("durata_ore")
    @classmethod
    def durata_valida(cls, v):
        if v not in [1, 6, 12, 24]:
            raise ValueError("Durata deve essere 1, 6, 12 o 24 ore")
        return v

    @field_validator("visibilita")
    @classmethod
    def visibilita_valida(cls, v):
        if v not in ["tutti", "selezionati"]:
            raise ValueError("Visibilità deve essere 'tutti' o 'selezionati'")
        return v

    @field_validator("amici_invitati")
    @classmethod
    def amici_validi(cls, v):
        return [u.strip().lstrip("@").lower() for u in v if u.strip()]


class SfidaResponse(BaseModel):
    id: int
    autore: UtentePublicResponse            # ← pubblico
    tema: str
    durata_ore: int
    scadenza: datetime
    is_scaduta: bool
    visibilita: str = "tutti"
    vincitore: Optional[UtentePublicResponse]   # ← pubblico
    num_partecipanti: int
    ho_partecipato: bool = False
    invitati: List[UtentePublicResponse] = []   # ← pubblico
    creato_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
# NOTIFICA
# ============================================================
class NotificaResponse(BaseModel):
    id: int
    tipo: str
    testo: str
    letta: bool
    mittente: Optional[UtentePublicResponse] = None  # ← pubblico
    richiesta_id: Optional[int] = None
    stato_richiesta: Optional[str] = None
    creato_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
# BADGE
# ============================================================
class BadgeResponse(BaseModel):
    tipo: str
    sbloccato_at: datetime

    model_config = {"from_attributes": True}


# Aggiorna riferimenti circolari
TokenResponse.model_rebuild()
CommentoResponse.model_rebuild()