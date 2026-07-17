from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _engine_options(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            echo=settings.debug,
            future=True,
            **_engine_options(settings.database_url),
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )
    return _session_factory


def get_db_session() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=get_engine())
    _apply_compat_schema_updates(get_engine())


def _apply_compat_schema_updates(engine: Engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "sources" not in table_names:
        return

    existing_columns = {column["name"] for column in inspector.get_columns("sources")}
    pending_columns = [
        ("organization_name", "TEXT"),
        ("source_type", "VARCHAR(64) NOT NULL DEFAULT 'admissions'"),
        ("collection_domain", "VARCHAR(64) NOT NULL DEFAULT 'admissions_notice'"),
        ("source_origin", "VARCHAR(32) NOT NULL DEFAULT 'manual'"),
        ("adapter_id", "INTEGER"),
        ("template_id", "INTEGER"),
        ("start_urls_json", "JSON NOT NULL DEFAULT '[]'"),
        ("onboarding_status", "VARCHAR(32) NOT NULL DEFAULT 'ready'"),
        ("confidence_score", "FLOAT"),
        ("entrypoint_url", "TEXT"),
        ("health_status", "VARCHAR(16) NOT NULL DEFAULT 'healthy'"),
        ("last_validated_at", "DATETIME"),
        ("last_discovered_at", "DATETIME"),
        ("last_success_at", "DATETIME"),
        ("last_failure_reason", "TEXT"),
        ("validation_evidence", "JSON NOT NULL DEFAULT '{}'"),
        ("scope_json", "JSON NOT NULL DEFAULT '{}'"),
        ("resolver_meta_json", "JSON NOT NULL DEFAULT '{}'"),
    ]
    statements = [
        f"ALTER TABLE sources ADD COLUMN {name} {definition}"
        for name, definition in pending_columns
        if name not in existing_columns
    ]
    index_statements = [
        "CREATE INDEX IF NOT EXISTS ix_sources_adapter_id ON sources (adapter_id)",
        "CREATE INDEX IF NOT EXISTS ix_sources_template_id ON sources (template_id)",
        "CREATE INDEX IF NOT EXISTS ix_sources_collection_domain ON sources (collection_domain)",
    ]

    document_columns = set()
    if "documents" in table_names:
        document_columns = {column["name"] for column in inspector.get_columns("documents")}
    document_statements = [
        f"ALTER TABLE documents ADD COLUMN {name} {definition}"
        for name, definition in [
            ("collection_domain", "VARCHAR(64) NOT NULL DEFAULT 'admissions_notice'"),
            ("content_category", "VARCHAR(128)"),
            ("institution_name", "VARCHAR(255)"),
        ]
        if "documents" in table_names and name not in document_columns
    ]
    document_index_statements = [
        "CREATE INDEX IF NOT EXISTS ix_documents_collection_domain ON documents (collection_domain)",
        "CREATE INDEX IF NOT EXISTS ix_documents_content_category ON documents (content_category)",
        "CREATE INDEX IF NOT EXISTS ix_documents_institution_name ON documents (institution_name)",
    ] if "documents" in table_names else []

    task_statements = []
    task_index_statements = []
    if "crawl_tasks" in table_names:
        task_columns = {column["name"] for column in inspector.get_columns("crawl_tasks")}
        for name, definition in [
            ("trace_id", "VARCHAR(36)"),
            ("idempotency_key", "VARCHAR(128)"),
            ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
            ("max_attempts", "INTEGER NOT NULL DEFAULT 3"),
        ]:
            if name not in task_columns:
                task_statements.append(
                    f"ALTER TABLE crawl_tasks ADD COLUMN {name} {definition}"
                )
        task_index_statements.extend(
            [
                "CREATE INDEX IF NOT EXISTS ix_crawl_tasks_trace_id ON crawl_tasks (trace_id)",
                (
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_crawl_tasks_source_id_idempotency_key "
                    "ON crawl_tasks (source_id, idempotency_key)"
                ),
            ]
        )

    directory_statements = []
    directory_index_statements = []
    if "university_directory" not in table_names:
        directory_statements.append(
            "CREATE TABLE university_directory ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "university_name VARCHAR(255) NOT NULL, "
            "normalized_name VARCHAR(255) NOT NULL, "
            "official_homepage_url TEXT NOT NULL, "
            "admissions_entry_url TEXT NOT NULL, "
            "entrypoint_candidates_json JSON NOT NULL DEFAULT '[]', "
            "selected_entrypoint_url TEXT, "
            "scope VARCHAR(64) NOT NULL DEFAULT 'graduate_admissions', "
            "status VARCHAR(16) NOT NULL DEFAULT 'active', "
            "last_checked_at DATETIME NOT NULL, "
            "created_at DATETIME NOT NULL, "
            "updated_at DATETIME NOT NULL"
            ")"
        )
        directory_index_statements.extend(
            [
                "CREATE INDEX IF NOT EXISTS ix_university_directory_id ON university_directory (id)",
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_university_directory_normalized_name ON university_directory (normalized_name)",
            ]
        )
    else:
        directory_columns = {column["name"] for column in inspector.get_columns("university_directory")}
        for name, definition in [
            ("entrypoint_candidates_json", "JSON NOT NULL DEFAULT '[]'"),
            ("selected_entrypoint_url", "TEXT"),
        ]:
            if name not in directory_columns:
                directory_statements.append(f"ALTER TABLE university_directory ADD COLUMN {name} {definition}")

    if (
        not statements
        and not index_statements
        and not document_statements
        and not document_index_statements
        and not task_statements
        and not task_index_statements
        and not directory_statements
        and not directory_index_statements
    ):
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        for statement in index_statements:
            connection.execute(text(statement))
        for statement in document_statements:
            connection.execute(text(statement))
        for statement in document_index_statements:
            connection.execute(text(statement))
        for statement in task_statements:
            connection.execute(text(statement))
        for statement in task_index_statements:
            connection.execute(text(statement))
        for statement in directory_statements:
            connection.execute(text(statement))
        for statement in directory_index_statements:
            connection.execute(text(statement))


def reset_database_state() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
