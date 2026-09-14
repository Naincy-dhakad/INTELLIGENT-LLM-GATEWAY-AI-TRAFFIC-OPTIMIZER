import asyncio

import pytest

from gateway.infrastructure.database import session as database_session
from gateway.infrastructure.database.session import (
    DatabaseResource,
    DatabaseStartupError,
    DatabaseStartupFailureCategory,
)
from gateway.infrastructure.server_runtime import GatewayRuntime, RuntimeState


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, revision_rows):
        self.revision_rows = revision_rows
        self.queries = []

    def execute(self, statement):
        query = str(statement)
        self.queries.append(query)
        if "alembic_version" in query:
            return FakeResult(self.revision_rows)
        return FakeResult([])

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeEngine:
    def __init__(self, revision_rows):
        self.connection = FakeConnection(revision_rows)
        self.dispose_calls = 0

    def connect(self):
        return self.connection

    def dispose(self):
        self.dispose_calls += 1


class FakeScript:
    def __init__(self, heads):
        self.heads = heads

    def get_heads(self):
        return self.heads


class FakeInspector:
    def __init__(self, table_available=True):
        self.table_available = table_available

    def has_table(self, name):
        return self.table_available and name == "gateway_usage_records"


def resource(monkeypatch, revision_rows=(("0001_create_usage_records",),), heads=("0001_create_usage_records",), table_available=True):
    engine = FakeEngine(revision_rows)
    instance = DatabaseResource(engine)
    monkeypatch.setattr(instance, "_script_directory", lambda: FakeScript(heads))
    monkeypatch.setattr(database_session, "inspect", lambda _connection: FakeInspector(table_available))
    return instance, engine


def test_valid_resource_checks_revision_and_required_table(monkeypatch):
    instance, engine = resource(monkeypatch)

    instance.validate_startup()

    assert any("SELECT 1" in query for query in engine.connection.queries)
    assert any("alembic_version" in query for query in engine.connection.queries)


def test_unavailable_database_fails_with_safe_category():
    class UnavailableEngine:
        def connect(self):
            raise RuntimeError("postgres password=must-not-leak")

        def dispose(self):
            pass

    instance = DatabaseResource(UnavailableEngine())

    with pytest.raises(DatabaseStartupError) as raised:
        instance.validate_startup()

    assert raised.value.category is DatabaseStartupFailureCategory.DATABASE_UNAVAILABLE
    assert "postgres" not in str(raised.value)
    assert "password" not in str(raised.value)


def test_schema_behind_fails_with_safe_category(monkeypatch):
    instance, _ = resource(monkeypatch, (("old_revision",),))

    with pytest.raises(DatabaseStartupError) as raised:
        instance.validate_startup()

    assert raised.value.category is DatabaseStartupFailureCategory.DATABASE_SCHEMA_INCOMPATIBLE
    assert "old_revision" not in str(raised.value)


def test_missing_usage_table_fails_safely(monkeypatch):
    instance, _ = resource(monkeypatch, table_available=False)

    with pytest.raises(DatabaseStartupError) as raised:
        instance.validate_startup()

    assert raised.value.category is DatabaseStartupFailureCategory.DATABASE_SCHEMA_INCOMPATIBLE


def test_multiple_heads_fail_without_selecting_one(monkeypatch):
    instance, _ = resource(monkeypatch, heads=("head_a", "head_b"))

    with pytest.raises(DatabaseStartupError) as raised:
        instance.validate_startup()

    assert raised.value.category is DatabaseStartupFailureCategory.DATABASE_MIGRATION_HEADS_INVALID


def test_runtime_validates_before_listener_and_disposes_on_shutdown(monkeypatch):
    instance, engine = resource(monkeypatch)

    class Server:
        def __init__(self):
            self.serve_calls = 0
            self.shutdown_calls = 0
            self.stopped = asyncio.Event()

        async def serve(self):
            self.serve_calls += 1
            await self.stopped.wait()

        async def shutdown(self):
            self.shutdown_calls += 1
            self.stopped.set()

    server = Server()
    runtime = GatewayRuntime(
        public_app=None,
        metrics_state=None,
        management_spec=None,
        management_server=None,
        public_server=server,
        database_resource=instance,
    )

    async def scenario():
        await runtime.start()
        assert server.serve_calls == 1
        await runtime.shutdown()

    asyncio.run(scenario())
    assert engine.dispose_calls == 1
    assert runtime.state is RuntimeState.STOPPED
