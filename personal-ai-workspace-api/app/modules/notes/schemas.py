from typing import Annotated
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator
)

NoteTitle=Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=200
    ),
]
NoteContent=Annotated[
    str,
    Field(max_length=50_000)
]

class WriteSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

class NoteCreate(WriteSchema):
    title: NoteTitle
    content: NoteContent | None = None

class NoteUpdate(WriteSchema):
    title: NoteTitle | None = None
    content: NoteContent | None = None
    @model_validator(mode="after")
    # @classmethod
    def validate_patch_semantics(self)->Self:
        if not self.model_fields_set:
            raise ValueError("Provide atleast one filed to update")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("Title cannot be null")

        return self

class NoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    content: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

class PaginationMeta(BaseModel):
    limit: int=Field(ge=1, le=100)
    offset: int=Field(ge=0)
    total: int=Field(ge=0)

class NoteListResponse(BaseModel):
    items: list[NoteRead]
    Pagination: PaginationMeta