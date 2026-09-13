import asyncio

import pytest
from fastapi import FastAPI

from gateway.config.settings import Settings
from gateway.infrastructure.server_runtime import RuntimeState, RuntimeStateError, create_gateway_runtime


class FakeServer:
    def __init__(self, config, *, fail_serve=False):
        self.config = config
        self.fail_serve = fail_serve
        self.serve_calls = 0
        self.shutdown_calls = 0
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()

    async def serve(self):
        self.serve_calls += 1
        if self.fail_serve:
            raise OSError("bind secret must not leak")
        self.started.set()
        await self.stopped.wait()

    async def shutdown(self):
        self.shutdown_calls += 1
        self.stopped.set()


def settings(metrics=False, management=False):
    return Settings(_env_file=None, metrics_enabled=metrics, metrics_management_enabled=management)


def factories(fail_management=False):
    public = []
    management = []

    def public_factory(config):
        server = FakeServer(config)
        public.append(server)
        return server

    def management_factory(config):
        server = FakeServer(config, fail_serve=fail_management)
        management.append(server)
        return server

    return public_factory, management_factory, public, management


def test_startup_matrix_constructs_management_only_when_both_flags_enabled():
    async def scenario():
        for metrics, management_enabled in ((False, False), (True, False), (False, True)):
            public_factory, management_factory, public, management = factories()
            runtime = create_gateway_runtime(settings(metrics, management_enabled), public_server_factory=public_factory, management_server_factory=management_factory)
            assert runtime.public_server is public[0]
            assert runtime.management_server is None
            assert management == []
            await runtime.shutdown()

        public_factory, management_factory, public, management = factories()
        runtime = create_gateway_runtime(settings(True, True), public_server_factory=public_factory, management_server_factory=management_factory)
        assert runtime.management_server is management[0]
        assert runtime.management_spec is not None
        await runtime.shutdown()

    asyncio.run(scenario())


def test_start_and_shutdown_are_explicit_and_idempotent():
    async def scenario():
        public_factory, management_factory, public, management = factories()
        runtime = create_gateway_runtime(settings(True, True), public_server_factory=public_factory, management_server_factory=management_factory)
        assert runtime.state is RuntimeState.CONSTRUCTED
        await runtime.start()
        assert runtime.state is RuntimeState.RUNNING
        assert public[0].serve_calls == 1
        assert management[0].serve_calls == 1
        with pytest.raises(RuntimeStateError):
            await runtime.start()
        await runtime.shutdown()
        await runtime.shutdown()
        assert runtime.state is RuntimeState.STOPPED
        assert public[0].shutdown_calls == 1
        assert management[0].shutdown_calls == 1

    asyncio.run(scenario())


def test_management_startup_failure_does_not_cancel_public_server():
    async def scenario():
        public_factory, management_factory, public, management = factories(fail_management=True)
        runtime = create_gateway_runtime(settings(True, True), public_server_factory=public_factory, management_server_factory=management_factory)
        await runtime.start()
        await asyncio.sleep(0)
        assert runtime.state is RuntimeState.RUNNING
        assert not public[0].stopped.is_set()
        assert runtime.management_failure == "startup_failure"
        await runtime.shutdown()

    asyncio.run(scenario())


def test_public_startup_failure_is_raised_and_management_is_cleaned_up():
    async def scenario():
        public = []
        management = []

        def public_factory(config):
            server = FakeServer(config, fail_serve=True)
            public.append(server)
            return server

        def management_factory(config):
            server = FakeServer(config)
            management.append(server)
            return server

        runtime = create_gateway_runtime(settings(True, True), public_server_factory=public_factory, management_server_factory=management_factory)
        with pytest.raises(OSError):
            await runtime.start()
        assert runtime.state is RuntimeState.STOPPED
        assert management[0].shutdown_calls == 1

    asyncio.run(scenario())


def test_runtime_construction_has_no_import_or_start_side_effects():
    public_factory, management_factory, public, management = factories()
    runtime = create_gateway_runtime(settings(), public_server_factory=public_factory, management_server_factory=management_factory)
    assert runtime.state is RuntimeState.CONSTRUCTED
    assert public[0].serve_calls == 0
    assert management == []
    assert "/metrics" not in {getattr(route, "path", None) for route in runtime.public_app.routes}


def test_public_and_management_apps_are_separate_and_shared_snapshot_source_exists():
    public_factory, management_factory, public, management = factories()
    runtime = create_gateway_runtime(settings(True, True), public_server_factory=public_factory, management_server_factory=management_factory)
    assert isinstance(runtime.public_app, FastAPI)
    assert runtime.management_spec is not None
    assert runtime.management_spec.app is not runtime.public_app
    runtime.metrics_state.increment("gateway_requests_total", {"route": "chat", "outcome": "success"})
    assert runtime.metrics_state.snapshot().counters[0].value == 1
