"""FastAPI app factory. `backcountry serve` runs it; tests build it with temp settings."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backcountry.api.endpoints import router
from backcountry.api.store import RegionStore, RunStore, Settings
from backcountry.planning import RequestError


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.default()
    app = FastAPI(title="Backcountry route engine", version="0.2")
    app.state.settings = settings
    app.state.regions = RegionStore(settings)
    app.state.runs = RunStore(settings.runs_dir) if settings.runs_dir else None
    app.include_router(router)

    @app.exception_handler(RequestError)
    def bad_request(request: Request, exc: RequestError):
        return JSONResponse(status_code=400, content={"error": str(exc), "field": exc.field})

    @app.exception_handler(KeyError)
    def not_found(request: Request, exc: KeyError):
        return JSONResponse(status_code=404, content={"error": exc.args[0] if exc.args else "not found"})

    @app.exception_handler(FileNotFoundError)
    def not_cached(request: Request, exc: FileNotFoundError):
        return JSONResponse(status_code=404, content={"error": str(exc)})

    return app
