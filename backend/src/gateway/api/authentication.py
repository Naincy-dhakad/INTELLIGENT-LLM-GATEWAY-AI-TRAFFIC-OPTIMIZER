"""Gateway-edge API key authentication.

Caller credentials are hashed at startup and never cross the API boundary into
routing or provider execution.
"""

from dataclasses import dataclass
import hashlib
import secrets

from fastapi import Request

from gateway.api.errors import GatewayAPIError


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Minimal request principal; the caller secret is intentionally absent."""

    key_id: str


class GatewayAuthenticator:
    def __init__(self, enabled: bool, configured_keys: str | None) -> None:
        self._enabled = enabled
        self._key_hashes = tuple(
            self._digest(raw_key.strip())
            for raw_key in (configured_keys or "").split(",")
            if raw_key.strip()
        )

    @staticmethod
    def _digest(api_key: str) -> bytes:
        return hashlib.sha256(api_key.encode("utf-8")).digest()

    def authenticate(self, api_key: str | None) -> AuthenticatedPrincipal | None:
        if not self._enabled:
            return None
        if not api_key:
            raise self._failure()
        supplied = self._digest(api_key)
        for expected in self._key_hashes:
            if secrets.compare_digest(supplied, expected):
                return AuthenticatedPrincipal(key_id=expected.hex())
        raise self._failure()

    @staticmethod
    def _failure() -> GatewayAPIError:
        return GatewayAPIError(
            code="authentication_required",
            message="A valid API key is required.",
            status_code=401,
            retryable=False,
        )


def require_gateway_auth(request: Request) -> AuthenticatedPrincipal | None:
    authenticator: GatewayAuthenticator = request.app.state.gateway_authenticator
    principal = authenticator.authenticate(request.headers.get("X-API-Key"))
    if principal is not None:
        request.state.authenticated_principal = principal
    return principal
