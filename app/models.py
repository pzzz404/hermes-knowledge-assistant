from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

class ChatInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=2000)
    request_id: UUID

class Step(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(min_length=1, max_length=3)

class GeneratedAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['answered', 'needs_clarification', 'not_found']
    summary: str = Field(min_length=1, max_length=1800)
    steps: list[Step] = Field(default_factory=list, max_length=6)
    clarifying_question: str | None = Field(default=None, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode='after')
    def check_status(self):
        if self.status == 'answered' and not self.evidence_ids:
            raise ValueError('An answer needs evidence')
        if self.status == 'needs_clarification' and not self.clarifying_question:
            raise ValueError('A clarification needs a question')
        if self.status != 'answered' and self.steps:
            raise ValueError('Do not give a procedure when not answering')
        return self
