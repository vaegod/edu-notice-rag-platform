from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import get_session_factory, init_db
from app.services.source_catalog import sync_builtin_source_catalog
from app.services.source_sync import sync_real_sources


def main() -> None:
    init_db()
    session = get_session_factory()()
    try:
        print(sync_builtin_source_catalog(session))
        print(sync_real_sources(session))
    finally:
        session.close()


if __name__ == "__main__":
    main()
