from pydantic import Field, BaseModel,ConfigDict
from typing import Literal


Status= Literal["scheduled","recorded","transcribing","archived"]

class Meeting(BaseModel):
    id: int
    title: str = Field(max_length=60)
    status: Status = "scheduled"