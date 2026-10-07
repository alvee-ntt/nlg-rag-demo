from enum import StrEnum

from pydantic import BaseModel, Field


class ResponseType(StrEnum):
    ANSWER = "answer"
    GATHER_INFORMATION = "gather_information"
    SELECT_DOCUMENTS = "select_documents"
    EMAIL_APPROVAL = "email_approval"
    ESCALATION = "escalation"
    CLARIFICATION = "clarification"
    WORKFLOW_COMPLETE = "workflow_complete"


class RiskLevel(StrEnum):
    NORMAL = "normal"
    SENSITIVE = "sensitive"
    HIGH = "high"


class CitationSpan(BaseModel):
    start_index: int
    end_index: int


class Citation(BaseModel):
    citation_id: str
    source_title: str
    section: str | None = None
    excerpt: str | None = None
    uri: str | None = None
    document_id: str | None = None
    annotation_indexes: list[int] = Field(default_factory=list)
    spans: list[CitationSpan] = Field(default_factory=list)


class Risk(BaseModel):
    area: str = "general"
    level: RiskLevel = RiskLevel.NORMAL


class FollowUpQuestion(BaseModel):
    id: str
    label: str
    type: str = "text"
    options: list[str] = Field(default_factory=list)


class Suggestion(BaseModel):
    action: str
    label: str


class AgentReply(BaseModel):
    response_type: ResponseType = ResponseType.ANSWER
    content: str
    citations: list[Citation] = Field(default_factory=list)
    risk: Risk = Field(default_factory=Risk)
    questions: list[FollowUpQuestion] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)
    warning: str | None = None


class LanguageReview(BaseModel):
    passed: bool
    violations: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10_000)
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply: AgentReply


class SessionSummary(BaseModel):
    session_id: str
    topic: str | None = None
    workflow_status: str = "normal"
