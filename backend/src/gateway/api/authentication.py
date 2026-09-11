"""Gateway-edge API key authentication.

Caller credentials are hashed at startup and never cross the API boundary into
routing or provider execution.
"""

from dataclasses import dataclass
import hashlib
import secrets

from fastapi import Request

from gateway.api.errors import GatewayAPIError
from gateway.application.observability_events import EventType, make_event


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

    @property
    def enabled(self) -> bool:
        return self._enabled

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


def _emit_authentication_result(request: Request, outcome: str, status_code: int) -> None:
    try:
        request.app.state.observability.emit(
            make_event(
                EventType.AUTHENTICATION_RESULT,
                request.state.request_id,
                outcome=outcome,
                route=request.url.path[:128],
                status_code=status_code,
            )
        )
    except Exception:
        pass


def require_gateway_auth(request: Request) -> AuthenticatedPrincipal | None:
    authenticator: GatewayAuthenticator = request.app.state.gateway_authenticator
    if not authenticator.enabled:
        _emit_authentication_result(request, "disabled", 200)
        return None
    try:
        principal = authenticator.authenticate(request.headers.get("X-API-Key"))
    except GatewayAPIError:
        _emit_authentication_result(request, "failure", 401)
        raise
    _emit_authentication_result(request, "success", 200)
    if principal is not None:
        request.state.authenticated_principal = principal
    return principal
