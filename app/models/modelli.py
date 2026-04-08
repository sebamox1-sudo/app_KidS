from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import enum

class Utente(Base):
    __tablename__ = "utenti"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(100), nullable=False)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(200), unique=True, index=True, nullable=False)
    password_hash = Column(String(200), nullable=True)
    bio = Column(String(300), default="")
    foto_profilo = Column(String(500), nullable=True)
    is_privato = Column(Boolean, default=False)
    onboarding_completato = Column(Boolean, default=False)
    
    # --- ECCO IL TASTO DI EMERGENZA ---
    is_banned = Column(Boolean, default=False)
    # ----------------------------------

    # --- STATISTICHE PER I BADGE (Nuove colonne) ---
    sfide_partecipate = Column(Integer, default=0)
    sfide_vinte = Column(Integer, default=0)
    voti_dati = Column(Integer, default=0)
    voti_negativi = Column(Integer, default=0) # ✨ AGGIUNTO PER IL BADGE "OCCHIO FINO"
    commenti_scritti = Column(Integer, default=0)
    like_ricevuti = Column(Integer, default=0)
    miglior_media = Column(Float, default=0.0)
    ha_preso_dieci = Column(Boolean, default=False)
    sfide_rapide = Column(Integer, default=0) # ✨ Per il badge "Flash"
    # -----------------------------------------------

    creato_at = Column(DateTime(timezone=True), server_default=func.now())
    aggiornato_at = Column(DateTime(timezone=True), onupdate=func.now())

    post = relationship("Post", back_populates="autore", cascade="all, delete")
    sondaggi = relationship("Sondaggio", back_populates="autore", cascade="all, delete")
    sfide_create = relationship("Sfida", back_populates="autore", foreign_keys="Sfida.autore_id", cascade="all, delete")
    like = relationship("Like", back_populates="utente", cascade="all, delete")
    commenti = relationship("Commento", back_populates="autore", cascade="all, delete")
    badge = relationship("BadgeUtente", back_populates="utente", cascade="all, delete")
    notifiche = relationship("Notifica", back_populates="destinatario", foreign_keys="Notifica.destinatario_id", cascade="all, delete")
    follower_rel = relationship("Follow", foreign_keys="Follow.seguito_id", back_populates="seguito")
    seguiti_rel = relationship("Follow", foreign_keys="Follow.follower_id", back_populates="follower")
    streak = relationship("Streak", back_populates="utente", uselist=False, cascade="all, delete")

    @property
    def num_follower(self):
        return len(self.follower_rel)

    @property
    def num_seguiti(self):
        return len(self.seguiti_rel)
    
    @property
    def num_post(self): # ✨ AGGIUNTO PER IL BADGE DEI POST
        return len(self.post)
    
    @property
    def streak_giorni(self):
        # Se l'utente ha una streak attiva, restituisce i giorni, altrimenti 0
        return self.streak.giorni if self.streak else 0


class Follow(Base):
    __tablename__ = "follow"

    id = Column(Integer, primary_key=True)
    follower_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), nullable=False)
    seguito_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), nullable=False)
    creato_at = Column(DateTime(timezone=True), server_default=func.now())

    follower = relationship("Utente", foreign_keys=[follower_id], back_populates="seguiti_rel")
    seguito = relationship("Utente", foreign_keys=[seguito_id], back_populates="follower_rel")


class Post(Base):
    __tablename__ = "post"

    id = Column(Integer, primary_key=True, index=True)
    autore_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), nullable=False)
    foto_principale = Column(String(500), nullable=True)
    foto_selfie = Column(String(500), nullable=True)
    testo = Column(Text, nullable=True)
    hashtag = Column(String(500), default="")
    amici_taggati = Column(String(500), default="")
    creato_at = Column(DateTime(timezone=True), server_default=func.now())

    autore = relationship("Utente", back_populates="post")
    like = relationship("Like", back_populates="post", cascade="all, delete")
    commenti = relationship("Commento", back_populates="post", cascade="all, delete")
    voti = relationship("Voto", back_populates="post", cascade="all, delete")

    @property
    def num_like(self):
        return len(self.like)

    @property
    def media_voti(self):
        if not self.voti:
            return None
        return sum(v.voto for v in self.voti) / len(self.voti)


class Like(Base):
    __tablename__ = "like"

    id = Column(Integer, primary_key=True)
    utente_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), nullable=False)
    post_id = Column(Integer, ForeignKey("post.id", ondelete="CASCADE"), nullable=False)
    creato_at = Column(DateTime(timezone=True), server_default=func.now())

    utente = relationship("Utente", back_populates="like")
    post = relationship("Post", back_populates="like")


class Voto(Base):
    __tablename__ = "voti"

    id = Column(Integer, primary_key=True)
    utente_id = Column(Integer, ForeignKey("utenti.id", ondelete="SET NULL"), nullable=True)
    post_id = Column(Integer, ForeignKey("post.id", ondelete="CASCADE"), nullable=False)
    voto = Column(Float, nullable=False)
    anonimo = Column(Boolean, default=False)
    creato_at = Column(DateTime(timezone=True), server_default=func.now())

    utente = relationship("Utente")
    post = relationship("Post", back_populates="voti")


class Commento(Base):
    __tablename__ = "commenti"

    id = Column(Integer, primary_key=True, index=True)
    autore_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), nullable=False)
    post_id = Column(Integer, ForeignKey("post.id", ondelete="CASCADE"), nullable=False)
    testo = Column(Text, nullable=False)
    risposta_a_id = Column(Integer, ForeignKey("commenti.id", ondelete="CASCADE"), nullable=True)
    creato_at = Column(DateTime(timezone=True), server_default=func.now())

    autore = relationship("Utente", back_populates="commenti")
    post = relationship("Post", back_populates="commenti")
    risposte = relationship("Commento", backref="genitore", remote_side=[id])


class Sondaggio(Base):
    __tablename__ = "sondaggi"

    id = Column(Integer, primary_key=True, index=True)
    autore_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), nullable=False)
    domanda = Column(String(200), nullable=False)
    opzioni = Column(Text, nullable=False)
    creato_at = Column(DateTime(timezone=True), server_default=func.now())
    scadenza = Column(DateTime(timezone=True), nullable=False)

    autore = relationship("Utente", back_populates="sondaggi")
    voti = relationship("VotoSondaggio", back_populates="sondaggio", cascade="all, delete")



class VotoSondaggio(Base):
    __tablename__ = "voti_sondaggio"

    id = Column(Integer, primary_key=True)
    utente_id = Column(Integer, ForeignKey("utenti.id", ondelete="CSET NULL"), nullable=True)
    sondaggio_id = Column(Integer, ForeignKey("sondaggi.id", ondelete="CASCADE"), nullable=False)
    opzione_index = Column(Integer, nullable=False)
    anonimo = Column(Boolean, default=False)
    creato_at = Column(DateTime(timezone=True), server_default=func.now())

    sondaggio = relationship("Sondaggio", back_populates="voti")


class Sfida(Base):
    __tablename__ = "sfide"

    id = Column(Integer, primary_key=True, index=True)
    autore_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), nullable=False)
    tema = Column(String(200), nullable=False)
    durata_ore = Column(Integer, nullable=False)
    scadenza = Column(DateTime(timezone=True), nullable=False)
    vincitore_id = Column(Integer, ForeignKey("utenti.id", ondelete="SET NULL"), nullable=True)
    visibilita = Column(String(20), default="tutti")  # "tutti" o "selezionati"
    creato_at = Column(DateTime(timezone=True), server_default=func.now())

    autore = relationship("Utente", foreign_keys=[autore_id], back_populates="sfide_create")
    vincitore = relationship("Utente", foreign_keys=[vincitore_id])
    partecipazioni = relationship("PartecipazioneSfida", back_populates="sfida", cascade="all, delete")
    inviti = relationship("InvitoSfida", back_populates="sfida", cascade="all, delete")

    @property
    def is_scaduta(self):
        from datetime import datetime, timezone
        return datetime.now(timezone.utc) > self.scadenza.replace(tzinfo=timezone.utc)


class InvitoSfida(Base):
    __tablename__ = "inviti_sfida"

    id = Column(Integer, primary_key=True)
    sfida_id = Column(Integer, ForeignKey("sfide.id", ondelete="CASCADE"), nullable=False)
    invitato_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), nullable=False)
    creato_at = Column(DateTime(timezone=True), server_default=func.now())

    sfida = relationship("Sfida", back_populates="inviti")
    invitato = relationship("Utente")


class PartecipazioneSfida(Base):
    __tablename__ = "partecipazioni_sfida"

    id = Column(Integer, primary_key=True)
    sfida_id = Column(Integer, ForeignKey("sfide.id", ondelete="CASCADE"), nullable=False)
    utente_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), nullable=False)
    foto_url = Column(String(500), nullable=False)
    creato_at = Column(DateTime(timezone=True), server_default=func.now())

    sfida = relationship("Sfida", back_populates="partecipazioni")
    utente = relationship("Utente")
    voti = relationship("VotoSfida", back_populates="partecipazione", cascade="all, delete")

    @property
    def media_voti(self):
        if not self.voti:
            return 0.0
        return sum(v.voto for v in self.voti) / len(self.voti)


class VotoSfida(Base):
    __tablename__ = "voti_sfida"

    id = Column(Integer, primary_key=True)
    partecipazione_id = Column(Integer, ForeignKey("partecipazioni_sfida.id", ondelete="CASCADE"), nullable=False)
    votante_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), nullable=False)
    voto = Column(Float, nullable=False)
    creato_at = Column(DateTime(timezone=True), server_default=func.now())

    partecipazione = relationship("PartecipazioneSfida", back_populates="voti")
    votante = relationship("Utente")


class Streak(Base):
    __tablename__ = "streak"

    id = Column(Integer, primary_key=True)
    utente_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), unique=True, nullable=False)
    giorni = Column(Integer, default=0)
    ultimo_post = Column(DateTime(timezone=True), nullable=True)
    record = Column(Integer, default=0)

    utente = relationship("Utente", back_populates="streak")


class BadgeUtente(Base):
    __tablename__ = "badge_utenti"

    id = Column(Integer, primary_key=True)
    utente_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), nullable=False)
    tipo = Column(String(50), nullable=False)
    sbloccato_at = Column(DateTime(timezone=True), server_default=func.now())

    utente = relationship("Utente", back_populates="badge")


class Notifica(Base):
    __tablename__ = "notifiche"

    id = Column(Integer, primary_key=True, index=True)
    destinatario_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), nullable=False)
    mittente_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), nullable=True)
    tipo = Column(String(50), nullable=False)
    testo = Column(String(500), nullable=False)
    letta = Column(Boolean, default=False)
    creato_at = Column(DateTime(timezone=True), server_default=func.now())

    destinatario = relationship("Utente", foreign_keys=[destinatario_id], back_populates="notifiche")
    mittente = relationship("Utente", foreign_keys=[mittente_id])


class TokenDispositivoFCM(Base):
    __tablename__ = "token_dispositivi"

    id = Column(Integer, primary_key=True)
    utente_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(500), nullable=False, unique=True)
    piattaforma = Column(String(20), nullable=False)
    aggiornato_at = Column(DateTime(timezone=True), server_default=func.now())


class RichiestaFollow(Base):
    __tablename__ = "richieste_follow"

    id = Column(Integer, primary_key=True, index=True)
    richiedente_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), nullable=False)
    destinatario_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), nullable=False)
    stato = Column(String(20), default="in_attesa")
    creato_at = Column(DateTime(timezone=True), server_default=func.now())

    richiedente = relationship("Utente", foreign_keys=[richiedente_id])
    destinatario = relationship("Utente", foreign_keys=[destinatario_id])

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
 
    id = Column(Integer, primary_key=True)
    utente_id = Column(Integer, ForeignKey("utenti.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(200), unique=True, nullable=False, index=True)
    scadenza = Column(DateTime(timezone=True), nullable=False)
    revocato = Column(Boolean, default=False)
    creato_at = Column(DateTime(timezone=True), server_default=func.now())
 
    utente = relationship("Utente")