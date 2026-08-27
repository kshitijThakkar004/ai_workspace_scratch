from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
from app.modules.notes.model import Note