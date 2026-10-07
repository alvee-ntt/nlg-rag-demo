from pydantic import BaseModel, Field


class PromptDefinitionCreate(BaseModel):
    key: str = Field(..., pattern=r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9_-]*)+$")
    purpose: str = Field(..., min_length=1, max_length=500)
    instructions: str = Field(..., min_length=1, max_length=200_000)


class PromptVersionCreate(BaseModel):
    instructions: str = Field(..., min_length=1, max_length=200_000)
    change_notes: str = Field(default="", max_length=2_000)


class PromptVersionSelect(BaseModel):
    version: int = Field(..., ge=1)
