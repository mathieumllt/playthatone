from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.sql import func
from database import Base

class Song(Base):
    __tablename__ = "songs"

    id       = Column(Integer, primary_key=True, index=True)
    title    = Column(String(200), nullable=False)
    artist   = Column(String(200), nullable=False)
    lyrics   = Column(Text, nullable=True)
    position = Column(Integer, default=0)   # ordre d'affichage
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Vote(Base):
    __tablename__ = "votes"

    id      = Column(Integer, primary_key=True, index=True)
    song_id = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
