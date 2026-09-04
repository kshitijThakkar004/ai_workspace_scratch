import pytest
from pydantic import ValidationError
from app.modules.notes.schemas import NoteUpdate

def test_patch_ommited_content_from_explicit_null()->None:
    ommited=NoteUpdate(title="New Title")
    cleared=NoteUpdate(content=None)

    assert ommited.model_dump(exclude_unset=True) == {"title":"New Title"} #Validating whether the output is as expected when only providing the limited/partial data for updation
    assert cleared.model_dump(exclude_unset=True) == {"content": None}

def test_patch_rejects_empty_body()->None:
    with pytest.raises(ValidationError):
        NoteUpdate()

def test_patch_rejects_empty_Title()->None:
    with pytest.raises(ValidationError):
        NoteUpdate(title=None)

def test_patch_rejects_blank_Title()->None:
    with pytest.raises(ValidationError):
        NoteUpdate(title=" ")

def test_title_trimming()->None:
    trimmed=NoteUpdate(title=" New Title ")
    assert trimmed.model_dump(exclude_unset=True) == {"title":"New Title"}

def test_maximum_length_title_rejected()->None:
    with pytest.raises(ValidationError):
        NoteUpdate(title="a"*201)

def test_maximum_content_length_rejected()->None:
    with pytest.raises(ValidationError):
        NoteUpdate(content="a"*50001)

def test_maximum_title_length_allowed()->None:
    
   test_tittle_length=NoteUpdate(title="a"*200)
   assert len(test_tittle_length.title)==200

def test_maximum_content_length_allowed()->None:
    
   test_content_length=NoteUpdate(content="a"*50000)
   assert len(test_content_length.content)==50000

def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        NoteUpdate(title="New Title", unexpected_field="value")