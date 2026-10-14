"""FastAPI app factory. `backcountry serve` runs it; tests build it with temp settings."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backcountry.api.endpoints import router
from backcountry.api.store import NotFound, RegionStore, RunStore, Settings
from backcountry.planning import RequestError
from backcountry.region import REPO_ROOT


def create_app(settings: Settings | None = None, web_dist: Path | None = None) -> FastAPI:
    """The API, plus the built web app at / when web/dist exists (so a phone gets both from one address)."""
    settings = settings or Settings.default()
    app = FastAPI(title="Backcountry route engine", version="0.2")
    app.state.settings = settings
    app.state.regions = RegionStore(settings)
    app.state.runs = RunStore(settings.runs_dir) if settings.runs_dir else None
    app.include_router(router)

    @app.exception_handler(RequestError)
    def bad_request(request: Request, exc: RequestError):
        return JSONResponse(status_code=400, content={"error": str(exc), "field": exc.field})

    @app.exception_handler(NotFound)
    def not_found(request: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"error": exc.args[0] if exc.args else "not found"})

    web_dist = REPO_ROOT / "web" / "dist" if web_dist is None else web_dist
    if web_dist.exists():
        app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="web")  # after the API routes
    else:
        @app.get("/", response_class=HTMLResponse)
        def root():
            return ("<html><body><p>Backcountry API is running. The web app isn't built yet "
                    "(run <code>npm run build</code> in <code>web/</code>). Try <a href='/api/regions'>/api/regions</a>."
                    "</p></body></html>")
    return app
