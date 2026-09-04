"""Pure helpers for configured normalized provider/model health metadata."""

from gateway.domain.provider import HealthStatus, ProviderHealth


def health_for_model(
    health_metadata: tuple[ProviderHealth, ...], model_id: str
) -> ProviderHealth:
    return next(
        (item for item in health_metadata if item.model_id == model_id),
        ProviderHealth(model_id=model_id, health_score=None, status=HealthStatus.UNKNOWN),
    )
