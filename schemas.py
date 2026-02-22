from pydantic import BaseModel
from typing import Optional

# ---- Songs ----

class SongCreate(BaseModel):
    title: str
    artist: str
    lyrics: Optional[str] = None
    position: int = 0

class SongUpdate(BaseModel):
    title: Optional[str] = None
    artist: Optional[str] = None
    position: Optional[int] = None

class LyricsUpdate(BaseModel):
    lyrics: str

class SongOut(BaseModel):
    id: int
    title: str
    artist: str
    lyrics: Optional[str] = None
    position: int
    votes: int = 0

    model_config = {"from_attributes": True}
