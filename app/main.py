from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from app.api.routes import ask, dashboard, documents, nl, sources, tasks
from app.core.config import get_settings
from app.core.database import get_session_factory, init_db
from app.core.logger import configure_logging
from app.services.c9_source_knowledge import C9SourceKnowledgeService
from app.services.source_catalog import sync_builtin_source_catalog
from app.services.source_sync import sync_real_sources
from app.services.task.scheduler import SchedulerService


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.debug)
    scheduler = SchedulerService()
    knowledge_service = C9SourceKnowledgeService()
    static_dir = Path(__file__).resolve().parent / "static"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_db()
        session = get_session_factory()()
        try:
            sync_builtin_source_catalog(session)
            knowledge_service.sync_from_file(
                session,
                sync_sources=settings.auto_sync_c9_source_knowledge,
            )
            if settings.auto_seed_real_sources:
                sync_real_sources(session)
        finally:
            session.close()
        if settings.scheduler_enabled:
            scheduler.start()
        yield
        scheduler.shutdown()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.include_router(sources.router, prefix=settings.api_v1_prefix)
    app.include_router(tasks.router, prefix=settings.api_v1_prefix)
    app.include_router(documents.router, prefix=settings.api_v1_prefix)
    app.include_router(nl.router, prefix=settings.api_v1_prefix)
    app.include_router(ask.router, prefix=settings.api_v1_prefix)
    app.include_router(dashboard.router, prefix=settings.api_v1_prefix)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
