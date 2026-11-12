from pydantic import Field, BaseModel,ConfigDict
from typing import Literal
Body = Literal["council","commission","board"]


#the read models don't re-check the length rules, so a row written before a rule (or by hand) can still be read
class governingBody(BaseModel):
    model_config = ConfigDict(from_attributes=True,extra="forbid")
    id: int
    name: str
    body: Body
    description: str


class CreateGoverningBodyDTO(BaseModel):
    #error at extra things not detailed below
    model_config = ConfigDict(extra="forbid")
    name: str =Field(max_length=60)
    body: Body
    description: str =Field(min_length= 5)

class UpdateGoverningBodyDTO(BaseModel):
    #error at extra things not detailed below
    model_config = ConfigDict(extra="forbid")
    name: str =Field(max_length=60)
    body: Body
    description: str =Field(min_length= 5)

class ListGoverningBodyDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    body: Body
    description: str
    meeting_count: int | None