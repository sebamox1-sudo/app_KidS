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
        if len(v) < 6:
            raise ValueError("Password troppo corta (min 6 caratteri)")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    utente: "UtenteResponse"


# ============================================================
# UTENTE
# ============================================================
class UtenteResponse(BaseModel):
    id: int
    nome: str
    username: str
    email: str
    bio: str
    foto_profilo: Optional[str]
    is_privato: bool
    num_follower: int
    num_seguiti: int
    num_post: int = 0
    streak_giorni: int
    onboarding_completato: bool
    creato_at: datetime

    model_config = {"from_attributes": True}


class AggiornaProfilo(BaseModel):
    nome: Optional[str] = None
    username: Optional[str] = None
    bio: Optional[str] = None
    is_privato: Optional[bool] = None


# ============================================================
# POST
# ============================================================
class PostResponse(BaseModel):
    id: int
    autore: UtenteResponse
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
    anonimo: bool = False

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
    autore: UtenteResponse
    testo: str
    risposta_a_id: Optional[int]
    risposte: List["CommentoResponse"] = []
    creato_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
# SONDAGGIO
# ============================================================
class SondaggioRequest(BaseModel):
    domanda: str
    opzioni: List[str]

    @field_validator("opzioni")
    @classmethod
    def opzioni_valide(cls, v):
        if len(v) < 2:
            raise ValueError("Servono almeno 2 opzioni")
        if len(v) > 4:
            raise ValueError("Massimo 4 opzioni")
        return [o.strip() for o in v if o.strip()]


class VotoSondaggioRequest(BaseModel):
    opzione_index: int
    anonimo: bool = False


class SondaggioResponse(BaseModel):
    id: int
    autore: UtenteResponse
    domanda: str
    opzioni: List[str]
    voti_per_opzione: List[int]
    totale_voti: int
    ho_votato: bool = False
    mia_opzione: Optional[int] = None
    creato_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
# SFIDA — con visibilità e lista amici
# ============================================================
class SfidaRequest(BaseModel):
    tema: str
    durata_ore: int
    visibilita: str = "tutti"  # "tutti" o "selezionati"
    amici_usernames: List[str] = []  # username degli amici invitati

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

    @field_validator("amici_usernames")
    @classmethod
    def amici_validi(cls, v):
        return [u.strip().lstrip("@").lower() for u in v if u.strip()]


class SfidaResponse(BaseModel):
    id: int
    autore: UtenteResponse
    tema: str
    durata_ore: int
    scadenza: datetime
    is_scaduta: bool
    visibilita: str = "tutti"
    vincitore: Optional[UtenteResponse]
    num_partecipanti: int
    ho_partecipato: bool = False
    sono_invitato: bool = False  # true se l'utente corrente è tra gli invitati
    invitati: List[UtenteResponse] = []
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
    mittente: Optional[UtenteResponse]
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