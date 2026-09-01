from pydantic import Field, BaseModel,ConfigDict
from typing import Literal
from datetime import date


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
    title: str = Field(max_length=60)
    status: Status = "scheduled"
    meeting_date : date
    transcript_available: bool


    
