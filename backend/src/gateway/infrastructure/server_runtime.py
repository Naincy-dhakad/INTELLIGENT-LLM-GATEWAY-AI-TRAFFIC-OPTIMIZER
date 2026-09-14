"""Explicit same-process public and management server runtime."""

import asyncio
from dataclasses import dataclass
from enum import StrEnum
import inspect
import logging
from typing import Callable

import uvicorn
from fastapi import FastAPI

from gateway.application.metrics import MetricsSnapshot
from gateway.application.observability import InMemoryMetrics
from gateway.config.settings import Settings, get_settings
from gateway.infrastructure.database.session import DatabaseResource, DatabaseStartupError
from gateway.infrastructure.management_listener import ManagementListenerSpec, create_management_listener
from gateway.infrastructure.management_server import create_management_server
from gateway.infrastructure.observability.prometheus import PrometheusMetricsExporter
from gateway.main import create_app

_LOGGER = logging.getLogger(__name__)


class RuntimeState(StrEnum):
    CREATED = "created"
    CONSTRUCTED = "constructed"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


class RuntimeStateError(RuntimeError):
    pass


class SharedMetricsState:
    """Shared event/metric sink with a read-only snapshot source."""

    def __init__(self) -> None:
        self._metrics = InMemoryMetrics()

    def emit(self, _event) -> None:
        pass

    def increment(self, metric, labels=None, value=1) -> None:
        self._metrics.increment(metric, labels, value)

    def observe(self, metric, value, labels=None) -> None:
        self._metrics.observe(metric, value, labels)

    def snapshot(self) -> MetricsSnapshot:
        return self._metrics.snapshot()


def _host_category(host: str) -> str:
    if host in {"127.0.0.1", "::1", "localhost"}:
        return "loopback"
    if host == "0.0.0.0" or host == "::":
        return "wildcard"
    return "private"


def _safe_log(event: str, *, outcome: str, host: str | None = None, port: int | None = None, error_category: str | None = None) -> None:
    fields = {"outcome": outcome}
    if host is not None:
        fields["bind_host_category"] = _host_category(host)
    if port is not None:
        fields["port"] = port
    if error_category is not None:
        fields["error_category"] = error_category
    _LOGGER.info("%s %s", event, fields)


async def _invoke(server: object, method: str) -> object:
    result = getattr(server, method)()
    if inspect.isawaitable(result):
        return await result
    return result


@dataclass
class GatewayRuntime:
    public_app: FastAPI
    metrics_state: SharedMetricsState
    management_spec: ManagementListenerSpec | None
    management_server: object | None
    public_server: object | None = None
    state: RuntimeState = RuntimeState.CONSTRUCTED
    public_task: asyncio.Task | None = None
    management_task: asyncio.Task | None = None
    management_failure: str | None = None
    database_resource: DatabaseResource | None = None
    _database_disposed: bool = False

    async def start(self) -> None:
        if self.state is not RuntimeState.CONSTRUCTED:
            raise RuntimeStateError(f"runtime cannot start from {self.state.value}")
        self.state = RuntimeState.STARTING
        try:
            if self.database_resource is not None:
                self.database_resource.validate_startup()
        except DatabaseStartupError as error:
            self.state = RuntimeState.STOPPED
            self._dispose_database()
            _safe_log("database_startup_check_failed", outcome="failure", error_category=error.category.value)
            raise
        if self.public_server is None:
            self.state = RuntimeState.STOPPED
            self._dispose_database()
            raise RuntimeStateError("public server is not constructed")
        if self.database_resource is not None:
            _safe_log("database_startup_check_succeeded", outcome="success")
        _safe_log("gateway_listener_starting", outcome="success")
        self.public_task = asyncio.create_task(_invoke(self.public_server, "serve"))
        self.public_task.add_done_callback(self._public_done)
        if self.management_server is not None and self.management_spec is not None:
            _safe_log(
                "management_listener_starting",
                outcome="success",
                host=self.management_spec.host,
                port=self.management_spec.port,
            )
            self.management_task = asyncio.create_task(_invoke(self.management_server, "serve"))
            self.management_task.add_done_callback(self._management_done)
        await asyncio.sleep(0)
        if self.public_task.done():
            error = self.public_task.exception()
            self.state = RuntimeState.STOPPED
            if error is not None:
                _safe_log("gateway_listener_start_failed", outcome="failure", error_category="startup_failure")
                await self._stop_management()
                self._dispose_database()
                raise error
        self.state = RuntimeState.RUNNING
        _safe_log("gateway_listener_started", outcome="success")

    async def supervise(self) -> None:
        if self.state is not RuntimeState.RUNNING:
            raise RuntimeStateError(f"runtime cannot supervise from {self.state.value}")
        if self.public_task is None:
            raise RuntimeStateError("public server task is not running")
        await self.public_task

    async def shutdown(self) -> None:
        if self.state is RuntimeState.STOPPED:
            return
        if self.state not in {RuntimeState.CONSTRUCTED, RuntimeState.STARTING, RuntimeState.RUNNING, RuntimeState.STOPPING}:
            raise RuntimeStateError(f"runtime cannot stop from {self.state.value}")
        self.state = RuntimeState.STOPPING
        await self._stop_management()
        await self._stop_server(self.public_server, self.public_task, "gateway_listener_stopped")
        self._dispose_database()
        self.state = RuntimeState.STOPPED

    def _dispose_database(self) -> None:
        if self.database_resource is not None and not self._database_disposed:
            self.database_resource.dispose()
            self._database_disposed = True

    async def _stop_management(self) -> None:
        if self.management_server is None and self.management_task is None:
            return
        try:
            await self._stop_server(self.management_server, self.management_task, "management_listener_stopped")
        except Exception:
            self.management_failure = "shutdown_failure"
            _safe_log("management_listener_stopped", outcome="failure", error_category="shutdown_failure")

    async def _stop_server(self, server: object | None, task: asyncio.Task | None, event: str) -> None:
        if server is not None:
            await _invoke(server, "shutdown")
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        _safe_log(event, outcome="success")

    def _public_done(self, task: asyncio.Task) -> None:
        if task.cancelled() or task.exception() is None:
            return
        _safe_log("gateway_listener_start_failed", outcome="failure", error_category="startup_failure")

    def _management_done(self, task: asyncio.Task) -> None:
        if task.cancelled() or task.exception() is None:
            _safe_log("management_listener_stopped", outcome="success")
            return
        self.management_failure = "startup_failure"
        _safe_log("management_listener_start_failed", outcome="failure", error_category="startup_failure")


def create_gateway_runtime(
    settings: Settings | None = None,
    *,
    public_server_factory: Callable[[uvicorn.Config], object] = uvicorn.Server,
    management_server_factory: Callable[[uvicorn.Config], object] = uvicorn.Server,
) -> GatewayRuntime:
    """Construct both server branches without starting or binding either one."""
    resolved = settings or get_settings()
    metrics_state = SharedMetricsState()
    public_app = create_app(observability=metrics_state)
    public_server = public_server_factory(uvicorn.Config(public_app))
    management_spec = create_management_listener(
        resolved,
        metrics_state.snapshot,
        PrometheusMetricsExporter(),
    )
    management_server = None
    management_failure = None
    if management_spec is not None:
        try:
            management_server = create_management_server(
                management_spec,
                server_factory=management_server_factory,
            )
        except Exception:
            management_failure = "startup_failure"
            _safe_log(
                "management_listener_start_failed",
                outcome="failure",
                host=management_spec.host,
                port=management_spec.port,
                error_category="startup_failure",
            )
    return GatewayRuntime(
        public_app=public_app,
        metrics_state=metrics_state,
        management_spec=management_spec,
        management_server=management_server,
        public_server=public_server,
        management_failure=management_failure,
        database_resource=getattr(public_app.state, "database_resource", None),
    )
