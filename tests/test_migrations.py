from __future__ import annotations

import importlib
from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine, inspect


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_original_sys_path = list(sys.path)
_trimmed_sys_path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
sys.path = _trimmed_sys_path
alembic_spec = importlib.util.find_spec("alembic")
sys.path = _original_sys_path
if alembic_spec is None:
    pytest.skip("alembic package is not installed in the current environment", allow_module_level=True)
sys.path = _trimmed_sys_path
command = importlib.import_module("alembic.command")
Config = importlib.import_module("alembic.config").Config
sys.path = _original_sys_path


def test_alembic_upgrade_head(tmp_path, monkeypatch):
    database_path = tmp_path / "migration_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {
        "sources",
        "crawl_tasks",
        "raw_pages",
        "documents",
        "document_tags",
        "attachments",
        "llm_logs",
        "nl_tasks",
    }.issubset(tables)
