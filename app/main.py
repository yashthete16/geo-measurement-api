"""Application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from app import __version__
from app.api import files
from app.config import Settings, get_settings
from app.database import Base, build_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

DESCRIPTION = """
Upload a **KML** file or a **zipped Shapefile**; the service extracts every feature and
measures polygons (**area**) and lines (**length**).

Geographic coordinates are never measured in degrees: each feature is projected to the
metric UTM zone it sits in first, and cross-checked against an ellipsoidal calculation.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = build_engine(settings.database_url)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title="Geospatial File Measurement API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.include_router(files.router)

    @app.get("/health", tags=["meta"], summary="Liveness probe")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
