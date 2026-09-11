import re
import secrets
import time

from gateway.application.observability import EventSink, NoopObservability
from gateway.application.observability_events import EventType, make_event
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _request_id(value: str | None) -> str:
    if value and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return f"req_{secrets.token_urlsafe(16)}"


def _outcome(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "success"
    if 400 <= status_code < 500:
        return "rejected"
    return "failure"


class RequestIDMiddleware:
    """Assigns correlation IDs and emits safe request lifecycle events."""

    def __init__(
        self,
        app: ASGIApp,
        observability: EventSink | None = None,
        environment: str = "development",
    ) -> None:
        self.app = app
        self._observability = observability or NoopObservability()
        self._environment = environment

    def _emit(self, event_type: EventType, request_id: str, **attributes: object) -> None:
        try:
            self._observability.emit(make_event(event_type, request_id, **attributes))
        except Exception:
            # Observability is strictly best effort and cannot affect the request.
            pass

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        incoming = headers.get(b"x-request-id", b"").decode("latin-1") or None
        request_id = _request_id(incoming)
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.monotonic()
        route = scope.get("path", "/")
        method = scope.get("method", "UNKNOWN")
        self._emit(
            EventType.REQUEST_STARTED,
            request_id,
            route=route[:128],
            method=method[:16],
            environment=self._environment,
        )
        completed = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal completed
            if message["type"] == "http.response.start":
                response_headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ]
                response_headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": response_headers}
                if not completed:
                    completed = True
                    self._emit(
                        EventType.REQUEST_COMPLETED,
                        request_id,
                        route=route[:128],
                        status_code=message["status"],
                        outcome=_outcome(message["status"]),
                        latency_ms=max(0, round((time.monotonic() - started) * 1000)),
                    )
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            if not completed:
                self._emit(
                    EventType.REQUEST_COMPLETED,
                    request_id,
                    route=route[:128],
                    status_code=500,
                    outcome="failure",
                    latency_ms=max(0, round((time.monotonic() - started) * 1000)),
                )
            raise
