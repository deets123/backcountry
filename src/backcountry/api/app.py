"""FastAPI app factory. `backcountry serve` runs it; tests build it with temp settings."""

from pathlib import Path

import logging
import secrets

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backcountry.api.area_worker import AreaWorker, Busy, NoIndex, NoRoom
from backcountry.api.endpoints import router
from backcountry.api.plan_jobs import PlanJobs
from backcountry.api.store import NotFound, RegionStore, RunStore, Settings
from backcountry.chat.models import ChatModel, ModelBadOutput, ModelTimeout, ModelUnavailable, model_from_env
from backcountry.planning import RequestError
from backcountry.region import REPO_ROOT


def create_app(settings: Settings | None = None, web_dist: Path | None = None, chat_model: ChatModel | None = None,
               dem_fetch=None) -> FastAPI:
    """The API, plus the built web app at / when web/dist exists (so a phone gets both from one address)."""
    settings = settings or Settings.default()
    app = FastAPI(title="Backcountry route engine", version="0.2")
    app.state.settings = settings
    app.state.regions = RegionStore(settings)
    app.state.runs = RunStore(settings.runs_dir) if settings.runs_dir else None
    app.state.areas = AreaWorker(settings, app.state.regions, app.state.runs, dem_fetch=dem_fetch)
    app.state.plans = PlanJobs(settings)
    app.state.areas.in_use = app.state.plans.regions_in_use  # never evict an area with a plan under way
    cached: list = [chat_model]

    def get_chat_model() -> ChatModel:
        if cached[0] is None:  # built from .env on first use, so the API starts even if the chat isn't set up
            try:
                cached[0] = model_from_env()
            except ValueError as e:
                raise ModelUnavailable(str(e)) from e
        return cached[0]

    app.state.chat_model = get_chat_model
    app.include_router(router)

    @app.exception_handler(RequestError)
    def bad_request(request: Request, exc: RequestError):
        return JSONResponse(status_code=400, content={"error": str(exc), "field": exc.field})

    @app.exception_handler(NotFound)
    def not_found(request: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"error": exc.args[0] if exc.args else "not found"})

    @app.exception_handler(RequestValidationError)
    def invalid(request: Request, exc: RequestValidationError):
        # FastAPI's own 422s (non-JSON body, missing query param, non-integer path part), in the API's own shape.
        first = exc.errors()[0] if exc.errors() else {}
        loc = [str(x) for x in first.get("loc", []) if x not in ("body", "query", "path")]
        return JSONResponse(status_code=400, content={"error": f"{'.'.join(loc) or 'request'}: {first.get('msg', 'invalid')}",
                                                      "field": loc[-1] if loc else "request"})

    @app.exception_handler(Busy)
    def busy(request: Request, exc: Busy):
        return JSONResponse(status_code=429, content={"error": str(exc)})

    @app.exception_handler(NoRoom)
    def no_room(request: Request, exc: NoRoom):
        return JSONResponse(status_code=507, content={"error": str(exc)})

    @app.exception_handler(NoIndex)
    def no_index(request: Request, exc: NoIndex):
        return JSONResponse(status_code=503, content={"error": str(exc)})

    @app.exception_handler(ModelUnavailable)
    def model_unavailable(request: Request, exc: ModelUnavailable):
        return JSONResponse(status_code=503, content={"error": str(exc)})

    @app.exception_handler(ModelTimeout)
    def model_timeout(request: Request, exc: ModelTimeout):
        return JSONResponse(status_code=504, content={"error": str(exc)})

    @app.exception_handler(ModelBadOutput)
    def model_bad_output(request: Request, exc: ModelBadOutput):
        return JSONResponse(status_code=502, content={"error": str(exc)})

    @app.exception_handler(Exception)
    def failed(request: Request, exc: Exception):
        error_id = secrets.token_hex(4)  # the traceback (which may name files) stays in the server log, under this id
        logging.getLogger("backcountry.api").exception("request failed (error %s)", error_id)
        return JSONResponse(status_code=500, content={"error": f"Something went wrong on the server (error {error_id}). Try again."})

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
