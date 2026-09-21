from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.modules.notes.model import Note

class NoteRepository:
    def __init__(self, session: Session)->None:
        self.session=session

    def add(self, note: Note)->None:
        self.session.add(note)          # Insert Into notes values()
                                        

    def get(self,note_id:UUID)->Note | None:
        return self.session.get(Note,note_id)   #select * from notes;

    def list_page(self, *, limit:int, offset:int)->list[Note]:
        statement=(select(Note)                 #select * from notes Order By created_at Desc, id Desc Limit limit Offset offset;
                   .order_by(Note.created_at.desc(), Note.id.desc())
                   .limit(limit)
                   .offset(offset)
                   )
        return list(self.session.scalars(statement).all())

    def count(self)->int:
        statement= select(func.count()).select_from(Note) #select count(*) from notes;
        return self.session.scalar(statement) or 0

    def delete(self,note:Note)->None:
        self.session.delete(note)       # Delete from note where note=note;
    