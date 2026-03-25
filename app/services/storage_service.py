import os
import io
import uuid
from PIL import Image
from supabase import create_client, Client
from fastapi import UploadFile, HTTPException

# 1. Recuperiamo le chiavi di Supabase (che hai impostato su Railway)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BUCKET_NAME = "kids-media"

# Inizializziamo il client in modo sicuro
supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Passiamo da 1080 a 1440px e alziamo la qualità del JPEG da 80 a 90 (meno compressione)
async def carica_e_comprimi_foto(file: UploadFile, cartella: str, max_size: int = 1440, qualita: int = 90) -> str:
    """
    Riceve un UploadFile da FastAPI, lo comprime in RAM e lo carica su Supabase.
    Restituisce l'URL pubblico permanente dell'immagine.
    """
    if not supabase:
        raise HTTPException(status_code=500, detail="Il server non è collegato a Supabase (chiavi mancanti).")

    try:
        # 2. Leggiamo l'immagine direttamente nella memoria RAM (addio disco di Railway!)
        contenuto = await file.read()
        immagine = Image.open(io.BytesIO(contenuto))
        
        # 3. Se l'immagine è un PNG con sfondo trasparente, la convertiamo in RGB per farla diventare un JPEG leggero
        if immagine.mode in ("RGBA", "P"):
            immagine = immagine.convert("RGB")
            
        # 4. Ridimensionamento intelligente: se è enorme, la rimpicciolisce mantenendo le proporzioni
        immagine.thumbnail((max_size, max_size))
        
        # 5. Salviamo la nuova immagine compressa in un "file virtuale" in memoria
        buffer = io.BytesIO()
        immagine.save(buffer, format="JPEG", quality=qualita, optimize=True)
        file_compresso = buffer.getvalue()
        
        # 6. Creiamo un nome unico e il percorso (es. "profili/123e4567.jpg")
        nome_file = f"{uuid.uuid4()}.jpg"
        percorso_supabase = f"{cartella}/{nome_file}"
        
        # 7. Spediamo il file a Supabase!
        supabase.storage.from_(BUCKET_NAME).upload(
            path=percorso_supabase,
            file=file_compresso,
            file_options={"content-type": "image/jpeg"}
        )
        
        # 8. Ci facciamo restituire l'URL pubblico da salvare nel database
        url_pubblico = supabase.storage.from_(BUCKET_NAME).get_public_url(percorso_supabase)
        return url_pubblico
        
    except Exception as e:
        print(f"Errore durante l'upload su Supabase: {e}")
        raise HTTPException(status_code=500, detail="Errore nel caricamento dell'immagine nel cloud.")