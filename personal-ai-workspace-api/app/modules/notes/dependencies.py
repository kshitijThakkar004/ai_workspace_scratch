from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.modules.notes.repository import NoteRepository
from app.modules.notes.service import NoteService


SessionDep= Annotated[Session, Depends(get_session)]

def get_note_service(session:SessionDep)->NoteService:
    repository=NoteRepository(session)
    return NoteService(repository,session)

NoteServiceDep= Annotated[NoteService, Depends(get_note_service)]