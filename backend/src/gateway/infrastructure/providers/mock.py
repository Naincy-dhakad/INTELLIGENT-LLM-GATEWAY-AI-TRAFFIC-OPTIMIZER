from decimal import Decimal

from gateway.domain.provider import (
    Capability,
    Provider,
    ProviderChatRequest,
    ProviderChatResponse,
    ProviderError,
    ProviderErrorCategory,
    ModelLatency,
    ModelPricing,
    ProviderMetadata,
    ProviderMessage,
)


class MockProvider:
    """Deterministic, in-process provider used only for contract testing."""

    def __init__(
        self,
        provider_id: str = "phase3-mock",
        input_usd_per_million_tokens: Decimal = Decimal("1"),
        output_usd_per_million_tokens: Decimal = Decimal("2"),
        estimated_latency_ms: int = 100,
    ) -> None:
        self._metadata = ProviderMetadata(
            id=provider_id,
            name="Phase 3 Mock Provider",
            capabilities=frozenset({Capability.TEXT_GENERATION}),
            model_ids=("phase3-mock-model",),
            supports_streaming=False,
            pricing=(
                ModelPricing(
                    model_id="phase3-mock-model",
                    input_usd_per_million_tokens=input_usd_per_million_tokens,
                    output_usd_per_million_tokens=output_usd_per_million_tokens,
                ),
            ),
            latency=(ModelLatency(model_id="phase3-mock-model", estimated_latency_ms=estimated_latency_ms),),
        )

    @property
    def metadata(self) -> ProviderMetadata:
        return self._metadata

    def chat(self, request: ProviderChatRequest) -> ProviderChatResponse:
        unsupported = request.required_capabilities - self.metadata.capabilities
        if unsupported:
            raise ProviderError(
                category=ProviderErrorCategory.UNSUPPORTED_CAPABILITY,
                message="The mock provider does not support the requested capability.",
            )

        selected_model = request.model or self.metadata.model_ids[0]
        return ProviderChatResponse(
            message=ProviderMessage(
                role="assistant",
                content="Mock response: this request was handled without a real provider.",
            ),
            provider_id=self.metadata.id,
            model=selected_model,
            finish_reason="stop",
            usage=None,
        )


def is_provider(value: object) -> bool:
    """Runtime assertion helper for tests and composition-root checks."""
    return isinstance(value, Provider)
