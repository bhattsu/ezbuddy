from pydantic import BaseModel, Field


class QuestionOptionItem(BaseModel):
    value: str
    label: str


class QuestionOptionsRequest(BaseModel):
    answers: dict[str, str] = Field(
        default_factory=dict,
        description="Workflow answers keyed by question_code (e.g. JURISDICTION)",
    )


class QuestionOptionsResponse(BaseModel):
    question_code: str
    options_source: str
    options: list[QuestionOptionItem]
