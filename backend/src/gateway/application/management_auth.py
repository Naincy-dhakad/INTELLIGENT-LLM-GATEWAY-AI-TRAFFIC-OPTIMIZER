"""Technology-neutral authorization boundary for the management application."""

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Literal, Protocol


class AuthorizationResult(StrEnum):
    ALLOWED = "allowed"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"


_ROLE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True)
class ManagementIdentity:
    authenticated: bool
    role: str | None = None
    auth_mode: Literal["mtls", "token"] | None = None

    def __post_init__(self) -> None:
        if self.auth_mode not in (None, "mtls", "token"):
            raise ValueError("invalid management authentication mode")
        if self.role is not None and not _ROLE.fullmatch(self.role):
            raise ValueError("invalid management role")


@dataclass(frozen=True)
class AuthorizationDecision:
    result: AuthorizationResult

    @property
    def allowed(self) -> bool:
        return self.result is AuthorizationResult.ALLOWED


class ManagementAuthorizationPort(Protocol):
    def authorize(self, identity: ManagementIdentity | None) -> AuthorizationDecision:
        ...


class MetricsManagementAuthorizer:
    """Small fixed policy for read-only metrics access."""

    def authorize(self, identity: ManagementIdentity | None) -> AuthorizationDecision:
        if identity is None or not identity.authenticated:
            return AuthorizationDecision(AuthorizationResult.UNAUTHORIZED)
        if identity.role != "metrics_reader":
            return AuthorizationDecision(AuthorizationResult.FORBIDDEN)
        return AuthorizationDecision(AuthorizationResult.ALLOWED)
