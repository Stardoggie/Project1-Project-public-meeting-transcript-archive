from pydantic import Field, BaseModel,ConfigDict
from typing import Literal


Body = Literal["council","commission","board"]


class governingBody(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str =Field(max_length=60)
    body: Body
    description: str =Field(min_length= 5)


class CreateGoverningBodyDTO(BaseModel):
    #error at extra things not detailed below
    model_config = ConfigDict(extra="forbid")

    name: str =Field(max_length=60)
    body: Body
    description: str =Field(min_length= 5)

