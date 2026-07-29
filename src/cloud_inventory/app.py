from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from cloud_inventory.api.dependencies import NetBoxMappingTargetResolver
from cloud_inventory.api.imports import router as imports_router
from cloud_inventory.api.mappings import router as mappings_router
from cloud_inventory.config import get_settings
from cloud_inventory.jobs.queue import JobQueue
from cloud_inventory.persistence.repositories import ImportRepository
from cloud_inventory.persistence.session import create_session_factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    engine, session_factory = create_session_factory(settings.database_url)
    app.state.import_repository = ImportRepository(session_factory)
    app.state.job_queue = JobQueue(session_factory)
    app.state.mapping_target_resolver = NetBoxMappingTargetResolver(
        str(settings.netbox_url),
        settings.netbox_token.get_secret_value(),
    )
    try:
        yield
    finally:
        await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="NetBox Cloud Inventory", lifespan=lifespan)
    app.include_router(imports_router)
    app.include_router(mappings_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
