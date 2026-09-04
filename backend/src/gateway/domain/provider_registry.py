from gateway.domain.provider import Provider


class ProviderRegistry:
    """Provider lookup only; it deliberately contains no routing policy."""

    def __init__(self, providers: tuple[Provider, ...] = (), default_provider_id: str | None = None):
        self._providers: dict[str, Provider] = {}
        for provider in providers:
            self.register(provider)
        self._default_provider_id = default_provider_id

    def register(self, provider: Provider) -> None:
        provider_id = provider.metadata.id
        if provider_id in self._providers:
            raise ValueError(f"provider already registered: {provider_id}")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> Provider | None:
        return self._providers.get(provider_id)

    @property
    def default_provider_id(self) -> str | None:
        return self._default_provider_id

    def default(self) -> Provider | None:
        if self._default_provider_id is None:
            return None
        return self.get(self._default_provider_id)

    def list(self) -> tuple[Provider, ...]:
        return tuple(self._providers.values())
