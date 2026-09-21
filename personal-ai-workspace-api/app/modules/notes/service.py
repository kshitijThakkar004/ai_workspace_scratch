from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.modules.notes.model import Note
from app.modules.notes.schemas import NoteCreate, NoteUpdate
from app.modules.notes.repository import NoteRepository

class NoteNotFoundError(Exception):
    def __init__(self, note_id:UUID)->None:
        self.note_id=note_id
        super().__init__(f"Note {note_id} was not found")

class NoteService:
    def __init__(self,session: Session, repository: NoteRepository)->None:
        self.session=session
        self.repository=repository

    def create(self,data:NoteCreate)->Note:
        note=Note(title=data.title, content=data.content)
        self.repository.add(note)
        self._commit()
        return note

    def list_page(self,*,offset:int,limit:int)->tuple[list[Note],int]:
        notes=self.repository.list_page(limit=limit, offset=offset)
        return notes, self.repository.count()

    def update(self, note_id:UUID,data:NoteUpdate)->Note:
        note=self._get_or_raise(note_id)
        changes=data.model_dump(exclude_unset=True)

        for field, value in changes.items():
            setattr(note, field, value)
        self._commit()
        self.session.refresh(note)
        return note

    def delete(self,note_id:UUID)->None:
        note=self.get_or_raise(note_id)
        self.repository.delete(note)
        self._commit()

    def _get_or_raise(self,note_id:UUID)->None:
        note=self.repository.get(note_id)
        if note is None:
            raise NoteNotFoundError(note_id)
        return note

    def _commit(self)->None:
        try: 
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise
    