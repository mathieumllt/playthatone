import os
import json
import re
import urllib.request
import urllib.parse
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from dotenv import load_dotenv

from database import engine, get_db, Base
from models import Song, Vote
from schemas import SongCreate, SongUpdate, LyricsUpdate, SongOut

load_dotenv()

ADMIN_TOKEN  = os.getenv("ADMIN_TOKEN", "rockNroll2024")
GENIUS_TOKEN = os.getenv("GENIUS_TOKEN", "")

# ── Bootstrap DB ────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    # Seed 2 chansons si la DB est vide
    db = next(get_db())
    if db.query(Song).count() == 0:
        seed = [
            Song(title="Johnny B. Goode", artist="Chuck Berry", position=0),
            Song(title="Whole Lotta Love", artist="Led Zeppelin", position=1),
        ]
        db.add_all(seed)
        db.commit()
    db.close()
    yield

app = FastAPI(title="PlayThatOne", lifespan=lifespan)

# ── WebSocket Manager ────────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, data: dict):
        message = json.dumps(data)
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)

manager = ConnectionManager()

# ── Helpers ──────────────────────────────────────────────────────────────────

def check_admin(authorization: str = Header(None)):
    if authorization != f"Bearer {ADMIN_TOKEN}":
        raise HTTPException(status_code=401, detail="Non autorisé")

def songs_with_votes(db: Session) -> List[dict]:
    songs = db.query(Song).order_by(Song.position).all()
    result = []
    for s in songs:
        count = db.query(func.count(Vote.id)).filter(Vote.song_id == s.id).scalar()
        result.append({
            "id": s.id,
            "title": s.title,
            "artist": s.artist,
            "lyrics": s.lyrics,
            "position": s.position,
            "votes": count,
        })
    return result

# ── Public routes ─────────────────────────────────────────────────────────────

@app.get("/songs", response_model=List[SongOut])
def get_songs(db: Session = Depends(get_db)):
    return songs_with_votes(db)


@app.post("/vote/{song_id}", status_code=201)
async def vote(song_id: int, db: Session = Depends(get_db)):
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Canción no encontrada")
    db.add(Vote(song_id=song_id))
    db.commit()
    await manager.broadcast({"event": "votes_update", "songs": songs_with_votes(db)})
    return {"ok": True}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, db: Session = Depends(get_db)):
    await manager.connect(ws)
    # Envoie l'état actuel dès la connexion
    await ws.send_text(json.dumps({"event": "votes_update", "songs": songs_with_votes(db)}))
    try:
        while True:
            await ws.receive_text()   # keep-alive, on ignore ce que le client envoie
    except WebSocketDisconnect:
        manager.disconnect(ws)

# ── Admin routes ──────────────────────────────────────────────────────────────

@app.get("/admin/songs")
def admin_get_songs(db: Session = Depends(get_db), _=Depends(check_admin)):
    return songs_with_votes(db)


@app.post("/admin/songs", status_code=201)
async def admin_add_song(
    body: SongCreate,
    db: Session = Depends(get_db),
    _=Depends(check_admin)
):
    song = Song(**body.model_dump())
    db.add(song)
    db.commit()
    db.refresh(song)
    await manager.broadcast({"event": "votes_update", "songs": songs_with_votes(db)})
    return song


@app.patch("/admin/songs/{song_id}")
async def admin_update_song(
    song_id: int,
    body: SongUpdate,
    db: Session = Depends(get_db),
    _=Depends(check_admin)
):
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Canción no encontrada")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(song, field, value)
    db.commit()
    await manager.broadcast({"event": "votes_update", "songs": songs_with_votes(db)})
    return song


@app.delete("/admin/songs/{song_id}", status_code=204)
async def admin_delete_song(
    song_id: int,
    db: Session = Depends(get_db),
    _=Depends(check_admin)
):
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Canción no encontrada")
    db.query(Vote).filter(Vote.song_id == song_id).delete()
    db.delete(song)
    db.commit()
    await manager.broadcast({"event": "votes_update", "songs": songs_with_votes(db)})


@app.post("/admin/songs/{song_id}/lyrics")
async def admin_update_lyrics(
    song_id: int,
    body: LyricsUpdate,
    db: Session = Depends(get_db),
    _=Depends(check_admin)
):
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Canción no encontrada")
    song.lyrics = body.lyrics
    db.commit()
    await manager.broadcast({"event": "votes_update", "songs": songs_with_votes(db)})
    return {"ok": True}


@app.post("/admin/reset")
async def admin_reset_votes(db: Session = Depends(get_db), _=Depends(check_admin)):
    db.query(Vote).delete()
    db.commit()
    await manager.broadcast({"event": "votes_reset", "songs": songs_with_votes(db)})
    return {"ok": True, "message": "Votes reiniciados"}


@app.get("/admin/stats")
def admin_stats(db: Session = Depends(get_db), _=Depends(check_admin)):
    total_votes = db.query(func.count(Vote.id)).scalar()
    connected   = len(manager.active)
    return {
        "total_votes": total_votes,
        "connected_clients": connected,
        "songs": songs_with_votes(db),
    }


# ── Genius API ───────────────────────────────────────────────────────────────

def genius_request(path: str) -> dict:
    url = f"https://api.genius.com{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {GENIUS_TOKEN}"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode())


def scrape_lyrics(song_url: str) -> str:
    req = urllib.request.Request(song_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        html = r.read().decode("utf-8", errors="ignore")
    containers = re.findall(r'data-lyrics-container="true"[^>]*>(.*?)</div>', html, re.DOTALL)
    if not containers:
        return ""
    text = "\n".join(containers)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&#x27;", "'").replace("&quot;", '"')
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


@app.get("/admin/genius/search")
def genius_search(q: str, _=Depends(check_admin)):
    if not GENIUS_TOKEN:
        raise HTTPException(status_code=503, detail="GENIUS_TOKEN non configuré")
    if not q or len(q) < 2:
        return []
    try:
        params = urllib.parse.urlencode({"q": q, "per_page": 8})
        data = genius_request(f"/search?{params}")
        hits = data.get("response", {}).get("hits", [])
        return [{
            "genius_id": h["result"].get("id"),
            "title":     h["result"].get("title", ""),
            "artist":    h["result"].get("primary_artist", {}).get("name", ""),
            "url":       h["result"].get("url", ""),
            "thumbnail": h["result"].get("song_art_image_thumbnail_url", ""),
        } for h in hits]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erreur Genius : {e}")


@app.get("/admin/genius/lyrics")
def genius_lyrics(url: str, _=Depends(check_admin)):
    if not GENIUS_TOKEN:
        raise HTTPException(status_code=503, detail="GENIUS_TOKEN non configuré")
    try:
        lyrics = scrape_lyrics(url)
        if not lyrics:
            raise HTTPException(status_code=404, detail="Paroles introuvables")
        return {"lyrics": lyrics}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erreur scraping : {e}")

# ── Static / SPA ──────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root():
    return FileResponse("static/index.html")

@app.get("/admin-panel")
def admin_panel():
    return FileResponse("static/admin.html")
