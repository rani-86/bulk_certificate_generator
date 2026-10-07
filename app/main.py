import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import service
from app.api import router
from app.config import Settings
from app.database import Base, make_engine, make_session_factory
from app import models  # noqa: F401  (registers tables on Base.metadata)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.certificates_dir.mkdir(parents=True, exist_ok=True)

    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.resume_unfinished_jobs:
            resumed = service.resume_unfinished_jobs(session_factory, settings.certificates_dir)
            if resumed:
                logging.getLogger(__name__).info("Resuming %d unfinished job(s)", len(resumed))
        yield
        engine.dispose()

    app = FastAPI(
        title="Bulk Certificate Generator",
        description="Submit a list of recipients, track progress, download PDF certificates.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.include_router(router)

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok"}

    return app


app = create_app()
