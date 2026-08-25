import uuid
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (
        CheckConstraint(
            "title ~ '[^[:space:]]'",
            name="ck_notes_title_not_blank",
    ),
    )
    id:Mapped[uuid.UUID]=mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    title:Mapped[str]=mapped_column(String(200),
                                    nullable=False)

    content:Mapped[str | None]=mapped_column(
        Text,
        CheckConstraint("length(content)<=50000",name="check_content_length"),
        nullable=True,
    )
    created_at:Mapped[datetime]=mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
                 )
    update_at:mapped_column[datetime]=mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now()
    )
    