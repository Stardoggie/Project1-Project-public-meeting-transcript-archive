from pydantic import Field,BaseModel,ConfigDict




class entityDTO(BaseModel):
     """
     used for validating entities
     """
     model_config = ConfigDict(from_attributes=True,extra="forbid")
     entity:str #entity that is in the dto, same with all below
     meeting_id:int
class CreateEntityDTO(BaseModel):
     """
        used to help create entities
     """
     model_config = ConfigDict(from_attributes=True,extra="forbid")
     entity:str
     entity_count:int = 0
     meeting_id: int |None = None



class phraseDTO(BaseModel):
     """
        used to validate keyphrases
     """
     model_config = ConfigDict(from_attributes=True,extra="forbid")
     phrase:str
     meeting_id:int


class CreatePhraseDTO(BaseModel):
     """
        used to help create
     """
     model_config = ConfigDict(from_attributes=True,extra="forbid")
     phrase:str
     meeting_id: int |None = None