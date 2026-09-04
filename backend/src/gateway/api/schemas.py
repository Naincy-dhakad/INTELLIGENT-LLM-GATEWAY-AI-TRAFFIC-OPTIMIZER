from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


Identifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
Capability = Literal[
    "text_generation",
    "reasoning",
    "coding",
    "structured_output",
    "tool_use",
    "streaming",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Message(StrictModel):
    role: Literal["system", "user", "assistant"]
    content: Annotated[str, StringConstraints(min_length=1, max_length=1_000_000)]


class Requirements(StrictModel):
    capabilities: list[Capability] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def capabilities_are_unique(self) -> "Requirements":
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("capabilities must not contain duplicates")
        return self


class RoutingOptions(StrictModel):
    objective: Literal["quality", "latency", "cost", "balanced"] = "balanced"
    max_cost_usd: Annotated[
        float, Field(ge=0, le=1_000_000, allow_inf_nan=False)
    ] | None = None
    max_latency_ms: Annotated[int, Field(gt=0, le=120_000)] | None = None


class GenerationOptions(StrictModel):
    max_output_tokens: Annotated[int, Field(gt=0, le=1_000_000)] | None = None
    temperature: Annotated[
        float, Field(ge=0, le=2, allow_inf_nan=False)
    ] | None = None
    top_p: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)] | None = None


class ChatRequest(StrictModel):
    messages: list[Message] = Field(min_length=1, max_length=100)
    model: Identifier | None = None
    provider: Identifier | None = None
    requirements: Requirements | None = None
    routing: RoutingOptions | None = None
    generation: GenerationOptions | None = None
    timeout_ms: Annotated[int, Field(gt=0, le=120_000)] | None = None
    metadata: dict[
        Annotated[
            str,
            StringConstraints(
                min_length=1,
                max_length=64,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
            ),
        ],
        Annotated[str, StringConstraints(max_length=256)],
    ] = Field(default_factory=dict, max_length=16)
    stream: bool = False

    @model_validator(mode="after")
    def request_is_bounded(self) -> "ChatRequest":
        total_chars = sum(len(message.content) for message in self.messages)
        if total_chars > 1_000_000:
            raise ValueError("combined message content is too large")
        return self


class ResponseMessage(StrictModel):
    role: Literal["assistant"]
    content: str


class ProviderInfo(StrictModel):
    id: Identifier
    model: Identifier


class Usage(StrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class RoutingMetadata(StrictModel):
    policy_version: str | None = None
    decision_reason: str | None = None
    fallback_used: bool = False
    category: Literal[
        "question_answer", "coding", "debugging", "summarization", "translation",
        "reasoning", "architecture_design", "creative_writing", "data_analysis",
        "conversation", "unknown",
    ] | None = None
    complexity_level: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    complexity_score: int | None = Field(default=None, ge=0, le=100)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    estimated_latency_ms: int | None = Field(default=None, gt=0, le=120_000)


class ErrorBody(StrictModel):
    code: str
    message: str
    request_id: Identifier
    retryable: bool = False
    details: dict[str, object] | None = None


class ErrorResponse(StrictModel):
    error: ErrorBody


class ChatResponse(StrictModel):
    id: Identifier
    object: Literal["chat.response"]
    status: Literal["completed"]
    message: ResponseMessage
    provider: ProviderInfo
    finish_reason: Literal[
        "stop", "length", "tool_call", "content_filter", "unknown"
    ]
    usage: Usage | None
    latency_ms: int = Field(ge=0)
    routing: RoutingMetadata
    request_id: Identifier
