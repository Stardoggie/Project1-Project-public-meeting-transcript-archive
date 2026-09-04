from pydantic import Field, BaseModel,ConfigDict
from typing import Literal
from datetime import date
from meeting_transcript.analysis.models import entityDTO,phraseDTO


Status= Literal["scheduled","recorded","transcribing","archived"]

class Meetings(BaseModel):
    model_config = ConfigDict(from_attributes=True,extra="forbid")
    id: int
    title: str = Field(max_length=60)
    status: Status = "scheduled"

class CreateMeetingsDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(max_length=60)
    status: Status = "scheduled"
    body_id: int | None = None
    meeting_date : date

class ListMeetingsDTO(BaseModel):
    model_config = ConfigDict(extra="forbid",from_attributes=True)
    id: int
    title: str = Field(max_length=60)
    status: Status = "scheduled"
    meeting_date : date
    transcript_available: bool
    key_phrases: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)

class UpdateMeetingsDTO(BaseModel):
    title: str  = Field(max_length=60)
    meeting_date : date
