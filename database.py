from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

import os
DATA_DIR = os.getenv("DATA_DIR", ".")
DATABASE_URL = f"sqlite:///{DATA_DIR}/playthatone.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
