from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class Capability(StrEnum):
    TEXT_GENERATION = "text_generation"
    REASONING = "reasoning"
    CODING = "coding"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_USE = "tool_use"
    STREAMING = "streaming"


class ProviderMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str = Field(min_length=1)


class ProviderGenerationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_output_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)


class ProviderChatRequest(BaseModel):
    """Provider-neutral input passed across the provider port."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[ProviderMessage, ...] = Field(min_length=1)
    model: str | None = None
    generation: ProviderGenerationSettings | None = None
    timeout_ms: int = Field(gt=0)
    deadline_monotonic: float = Field(gt=0)
    required_capabilities: frozenset[Capability] = frozenset()


class ProviderUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class ProviderChatResponse(BaseModel):
    """Normalized result; provider SDK response objects cannot cross this boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    message: ProviderMessage
    provider_id: str
    model: str
    finish_reason: str
    usage: ProviderUsage | None = None


class ProviderErrorCategory(StrEnum):
    TIMEOUT = "timeout"
    AUTHENTICATION_FAILURE = "authentication_failure"
    RATE_LIMIT = "rate_limit"
    INVALID_REQUEST = "invalid_request"
    UNAVAILABLE = "unavailable"
    PROVIDER_ERROR = "provider_error"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProviderError(Exception):
    """Safe, normalized provider failure; no native error or secret is retained."""

    category: ProviderErrorCategory
    message: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)


class ModelPricing(BaseModel):
    """Normalized pricing in USD per 1,000,000 input/output tokens."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str = Field(min_length=1, max_length=128)
    input_usd_per_million_tokens: Decimal = Field(ge=0)
    output_usd_per_million_tokens: Decimal = Field(ge=0)


class ModelLatency(BaseModel):
    """Configured estimated model latency in milliseconds, not measured latency."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str = Field(min_length=1, max_length=128)
    estimated_latency_ms: int = Field(gt=0, le=120_000)


class ProviderMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    capabilities: frozenset[Capability] = frozenset()
    model_ids: tuple[str, ...] = ()
    supports_streaming: bool = False
    pricing: tuple[ModelPricing, ...] = ()
    latency: tuple[ModelLatency, ...] = ()


@runtime_checkable
class Provider(Protocol):
    @property
    def metadata(self) -> ProviderMetadata:
        """Static, normalized provider catalog metadata."""
        ...

    def chat(self, request: ProviderChatRequest) -> ProviderChatResponse:
        """Execute one normalized chat request without retry or fallback."""
        ...
