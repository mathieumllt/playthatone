import os
import json
import re
import uuid
import urllib.request
import urllib.parse
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, Header, Request, Response, Cookie
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from dotenv import load_dotenv

from database import engine, get_db, Base
from models import Song, Vote
from schemas import SongCreate, SongUpdate, LyricsUpdate, SongOut

load_dotenv()

ADMIN_TOKEN  = os.getenv("ADMIN_TOKEN", "rockNroll2024")
GENIUS_TOKEN = os.getenv("GENIUS_TOKEN", "")

# ── Bootstrap DB ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
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

# ── WebSocket Manager ─────────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
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

# ── Helpers ───────────────────────────────────────────────────────────────────

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
async def vote(
    song_id: int,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    session_id: str = Cookie(default=None)
):
    # Créer un cookie de session si absent
    if not session_id:
        session_id = str(uuid.uuid4())

    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Canción no encontrada")

    # Vérifier si cette session a déjà voté
    existing = db.query(Vote).filter(Vote.session_id == session_id).first()
    if existing:
        resp = JSONResponse(
            status_code=409,
            content={"ok": False, "already_voted": True, "voted_for": existing.song_id}
        )
        resp.set_cookie("session_id", session_id, max_age=86400 * 30, httponly=True, samesite="lax")
        return resp

    db.add(Vote(song_id=song_id, session_id=session_id))
    db.commit()
    await manager.broadcast({"event": "votes_update", "songs": songs_with_votes(db)})

    resp = JSONResponse(status_code=201, content={"ok": True})
    resp.set_cookie("session_id", session_id, max_age=86400 * 30, httponly=True, samesite="lax")
    return resp


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, db: Session = Depends(get_db)):
    await manager.connect(ws)
    await ws.send_text(json.dumps({"event": "votes_update", "songs": songs_with_votes(db)}))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)

# ── Admin routes ──────────────────────────────────────────────────────────────

@app.get("/admin/songs")
def admin_get_songs(db: Session = Depends(get_db), _=Depends(check_admin)):
    return songs_with_votes(db)


@app.post("/admin/songs", status_code=201)
async def admin_add_song(body: SongCreate, db: Session = Depends(get_db), _=Depends(check_admin)):
    song = Song(**body.model_dump())
    db.add(song)
    db.commit()
    db.refresh(song)
    await manager.broadcast({"event": "votes_update", "songs": songs_with_votes(db)})
    return song


@app.patch("/admin/songs/{song_id}")
async def admin_update_song(song_id: int, body: SongUpdate, db: Session = Depends(get_db), _=Depends(check_admin)):
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Canción no encontrada")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(song, field, value)
    db.commit()
    await manager.broadcast({"event": "votes_update", "songs": songs_with_votes(db)})
    return song


@app.delete("/admin/songs/{song_id}", status_code=204)
async def admin_delete_song(song_id: int, db: Session = Depends(get_db), _=Depends(check_admin)):
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Canción no encontrada")
    db.query(Vote).filter(Vote.song_id == song_id).delete()
    db.delete(song)
    db.commit()
    await manager.broadcast({"event": "votes_update", "songs": songs_with_votes(db)})


@app.post("/admin/songs/{song_id}/lyrics")
async def admin_update_lyrics(song_id: int, body: LyricsUpdate, db: Session = Depends(get_db), _=Depends(check_admin)):
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
    # Broadcast avec reset_token pour que les clients effacent leur cookie de vote
    reset_token = str(uuid.uuid4())
    await manager.broadcast({
        "event": "votes_reset",
        "reset_token": reset_token,
        "songs": songs_with_votes(db)
    })
    return {"ok": True, "reset_token": reset_token}


@app.get("/admin/stats")
def admin_stats(db: Session = Depends(get_db), _=Depends(check_admin)):
    total_votes = db.query(func.count(Vote.id)).scalar()
    connected   = len(manager.active)
    return {
        "total_votes": total_votes,
        "connected_clients": connected,
        "songs": songs_with_votes(db),
    }


# ── Genius API ────────────────────────────────────────────────────────────────

def genius_request(path: str) -> dict:
    """Call Genius API via allorigins proxy to bypass outbound network restrictions."""
    target = f"https://api.genius.com{path}"
    # Encode auth header in the URL params so allorigins can forward it
    # Instead, call Genius directly first, fall back to proxy
    headers = {
        "Authorization": f"Bearer {GENIUS_TOKEN}",
        "User-Agent": "Mozilla/5.0"
    }
    # Try direct first
    try:
        req = urllib.request.Request(target, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as r:
            return json.loads(r.read().decode())
    except Exception:
        pass

    # Fallback: proxy via allorigins (GET only, no custom headers)
    # We use the Genius public search endpoint which accepts token in URL
    target_with_token = target + ("&" if "?" in target else "?") + f"access_token={GENIUS_TOKEN}"
    proxy_url = "https://api.allorigins.win/raw?url=" + urllib.parse.quote(target_with_token, safe="")
    req2 = urllib.request.Request(proxy_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req2, timeout=12) as r:
        return json.loads(r.read().decode())


def scrape_lyrics(song_url: str) -> str:
    """Scrape lyrics from Genius page via proxy if needed."""
    import gzip

    def fetch_html(url):
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", errors="ignore")

    # Try direct first
    try:
        html = fetch_html(song_url)
    except Exception:
        # Fallback via allorigins proxy
        proxy_url = "https://api.allorigins.win/raw?url=" + urllib.parse.quote(song_url, safe="")
        html = fetch_html(proxy_url)

    # Méthode 1 : data-lyrics-container
    containers = re.findall(
        r'data-lyrics-container="true"[^>]*>([\s\S]*?)</div>',
        html
    )
    if containers:
        text = "\n".join(containers)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"&#x27;", "'", text)
        text = re.sub(r"&quot;", '"', text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) > 100:
            return text

    # Méthode 2 : JSON embarqué dans la page
    json_match = re.search(r'"lyrics":\{"dom":\{"tag":"root","children":([\s\S]+?)\},"tracking_data"', html)
    if json_match:
        try:
            children = json.loads(json_match.group(1))
            def extract(node):
                if isinstance(node, str):
                    return node
                if isinstance(node, dict):
                    tag = node.get("tag", "")
                    children = node.get("children", [])
                    parts = [extract(c) for c in children]
                    if tag == "br":
                        return "\n"
                    return "".join(parts)
                if isinstance(node, list):
                    return "".join(extract(c) for c in node)
                return ""
            text = extract(children).strip()
            if len(text) > 100:
                return text
        except Exception:
            pass

    return ""


def search_chartlyrics(artist: str, title: str) -> str:
    """Search ChartLyrics API - free, no token needed."""
    try:
        params = urllib.parse.urlencode({"lyricsartist": artist, "lyricssong": title})
        url = f"https://api.chartlyrics.com/apiv1.asmx/SearchLyricDirect?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            xml = r.read().decode("utf-8", errors="ignore")
        # Extract lyrics from XML
        match = re.search("<Lyric>(.*?)</Lyric>", xml, re.DOTALL)
        if match:
            lyrics = match.group(1).strip()
            if len(lyrics) > 50:
                return lyrics
    except Exception:
        pass
    return ""


def search_chartlyrics_list(query: str) -> list:
    """Search ChartLyrics for song suggestions."""
    try:
        params = urllib.parse.urlencode({"lyricText": query})
        url = f"https://api.chartlyrics.com/apiv1.asmx/SearchLyric?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            xml = r.read().decode("utf-8", errors="ignore")
        results = []
        artists = re.findall("<Artist>(.*?)</Artist>", xml)
        songs_found = re.findall("<Song>(.*?)</Song>", xml)
        ids = re.findall("<LyricId>(.*?)</LyricId>", xml)
        for i in range(min(len(artists), len(songs_found), len(ids), 8)):
            if artists[i] and songs_found[i] and ids[i]:
                results.append({
                    "title": songs_found[i].strip(),
                    "artist": artists[i].strip(),
                    "chartlyrics_id": ids[i].strip(),
                })
        return results[:8]
    except Exception:
        return []


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
        raise HTTPException(status_code=502, detail=f"Erreur Genius search : {e}")


@app.get("/admin/genius/lyrics")
def genius_lyrics(url: str, _=Depends(check_admin)):
    if not GENIUS_TOKEN:
        raise HTTPException(status_code=503, detail="GENIUS_TOKEN non configuré")
    try:
        lyrics = scrape_lyrics(url)
        if not lyrics:
            raise HTTPException(status_code=404, detail="Paroles introuvables — essaie une autre version de la chanson")
        return {"lyrics": lyrics}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erreur scraping : {e}")


# ── ChartLyrics API (free fallback) ──────────────────────────────────────────

@app.get("/admin/chartlyrics/search")
def chartlyrics_search(q: str, _=Depends(check_admin)):
    if not q or len(q) < 2:
        return []
    results = search_chartlyrics_list(q)
    if not results:
        raise HTTPException(status_code=404, detail="Sin resultados en ChartLyrics")
    return results


@app.get("/admin/chartlyrics/lyrics")
def chartlyrics_lyrics(artist: str, title: str, _=Depends(check_admin)):
    lyrics = search_chartlyrics(artist, title)
    if not lyrics:
        raise HTTPException(status_code=404, detail="Paroles introuvables sur ChartLyrics")
    return {"lyrics": lyrics}


# ── Static / SPA ──────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root():
    return FileResponse("static/index.html")

@app.get("/admin-panel")
def admin_panel():
    return FileResponse("static/admin.html")
