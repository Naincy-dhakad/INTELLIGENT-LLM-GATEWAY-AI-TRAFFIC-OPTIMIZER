"""Owned SQLAlchemy resource and read-only database startup validation."""

from enum import StrEnum
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


class DatabaseStartupFailureCategory(StrEnum):
    DATABASE_UNAVAILABLE = "database_unavailable"
    DATABASE_SCHEMA_INCOMPATIBLE = "database_schema_incompatible"
    DATABASE_MIGRATION_HEADS_INVALID = "database_migration_heads_invalid"


class DatabaseStartupError(RuntimeError):
    """Safe startup failure; underlying database details are never exposed."""

    def __init__(self, category: DatabaseStartupFailureCategory) -> None:
        self.category = category
        super().__init__(category.value)


class DatabaseResource:
    """One owned engine/session resource shared by usage and budget operations."""

    def __init__(self, engine: Engine, migration_script_location: str | Path = "alembic") -> None:
        self.engine = engine
        self.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        self._migration_script_location = self._resolve_migration_location(migration_script_location)
        self._disposed = False

    @staticmethod
    def _resolve_migration_location(location: str | Path) -> str:
        if str(location) == "alembic":
            # session.py lives under backend/src/gateway/infrastructure/database.
            return str(Path(__file__).resolve().parents[4] / "alembic")
        return str(location)

    @classmethod
    def create(
        cls,
        database_url: str,
        migration_script_location: str | Path = "alembic",
    ) -> "DatabaseResource":
        try:
            engine = create_engine(database_url, pool_pre_ping=True)
        except Exception as exc:
            raise DatabaseStartupError(DatabaseStartupFailureCategory.DATABASE_UNAVAILABLE) from exc
        return cls(engine, migration_script_location)

    def validate_startup(self) -> None:
        self._check_connectivity()
        self._check_schema()

    def _check_connectivity(self) -> None:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:
            raise DatabaseStartupError(DatabaseStartupFailureCategory.DATABASE_UNAVAILABLE) from exc

    def _check_schema(self) -> None:
        try:
            script = self._script_directory()
            heads = tuple(script.get_heads())
            if len(heads) != 1:
                raise DatabaseStartupError(DatabaseStartupFailureCategory.DATABASE_MIGRATION_HEADS_INVALID)
            expected_head = heads[0]
            with self.engine.connect() as connection:
                revision_rows = connection.execute(text("SELECT version_num FROM alembic_version")).fetchall()
                if len(revision_rows) != 1 or revision_rows[0][0] != expected_head:
                    raise DatabaseStartupError(DatabaseStartupFailureCategory.DATABASE_SCHEMA_INCOMPATIBLE)
                if not inspect(connection).has_table("gateway_usage_records"):
                    raise DatabaseStartupError(DatabaseStartupFailureCategory.DATABASE_SCHEMA_INCOMPATIBLE)
        except DatabaseStartupError:
            raise
        except Exception as exc:
            raise DatabaseStartupError(DatabaseStartupFailureCategory.DATABASE_SCHEMA_INCOMPATIBLE) from exc

    def _script_directory(self) -> ScriptDirectory:
        config = Config()
        config.set_main_option("script_location", self._migration_script_location)
        return ScriptDirectory.from_config(config)

    def dispose(self) -> None:
        if not self._disposed:
            self.engine.dispose()
            self._disposed = True
