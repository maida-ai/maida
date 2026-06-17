"""
Minimal FastAPI server for the local viewer.

Serves trace (run) metadata and spans via OTel-based storage.
GET /api/runs, GET /api/runs/{trace_id}, GET /api/runs/{trace_id}/spans,
and GET / with static index.html.
"""

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

import maida.storage as storage
from maida.config import MaidaConfig, load_config
from maida.constants import SPEC_VERSION
from maida.events import spans_to_events

UI_STATIC_DIR = Path(__file__).resolve().parent / "ui_static"
UI_INDEX_PATH = UI_STATIC_DIR / "index.html"
UI_STYLES_PATH = UI_STATIC_DIR / "styles.css"
UI_APP_JS_PATH = UI_STATIC_DIR / "app.js"
FAVICON_PATH = UI_STATIC_DIR / "favicon.svg"


def _get_config(request: Request) -> MaidaConfig:
    return request.app.state.config


def _validated_trace_id(trace_id: str) -> str:
    try:
        return storage._validate_trace_id(trace_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid trace_id")


def create_app() -> FastAPI:
    app = FastAPI(title="Maida Viewer")
    app.state.config = load_config()

    class RenameRunRequest(BaseModel):
        run_name: str

    @app.get("/api/runs")
    def get_runs(config: MaidaConfig = Depends(_get_config)) -> dict:
        runs = storage.list_runs(limit=50, config=config)
        return {"spec_version": SPEC_VERSION, "runs": runs}

    @app.get("/api/runs/{trace_id}")
    def get_run_meta(trace_id: str, config: MaidaConfig = Depends(_get_config)) -> dict:
        trace_id = _validated_trace_id(trace_id)
        try:
            return storage.load_run_meta(trace_id, config)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found")

    @app.get("/api/runs/{trace_id}/spans")
    def get_run_spans(
        trace_id: str, config: MaidaConfig = Depends(_get_config)
    ) -> dict:
        trace_id = _validated_trace_id(trace_id)
        try:
            # TODO: cache or incrementally project events for large live traces.
            _, spans = storage.load_validated_run(trace_id, config)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid trace_id")
        except storage.RunValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))
        events = spans_to_events(spans)
        return {
            "spec_version": SPEC_VERSION,
            "trace_id": trace_id,
            "events": events,
            "spans": spans,
        }

    @app.get("/api/runs/{trace_id}/paths")
    def get_run_paths(
        trace_id: str, config: MaidaConfig = Depends(_get_config)
    ) -> dict:
        try:
            paths = storage.get_run_paths(trace_id, config)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid trace_id")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found")
        return {"spec_version": SPEC_VERSION, "trace_id": trace_id, "paths": paths}

    @app.get("/api/runs/{trace_id}/rename")
    def validate_run_for_rename(
        trace_id: str, config: MaidaConfig = Depends(_get_config)
    ) -> dict:
        trace_id = _validated_trace_id(trace_id)
        try:
            return storage.load_run_meta(trace_id, config)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found")

    @app.post("/api/runs/{trace_id}/rename")
    def rename_run(
        trace_id: str,
        payload: RenameRunRequest,
        config: MaidaConfig = Depends(_get_config),
    ) -> dict:
        try:
            return storage.rename_run(trace_id, payload.run_name, config)
        except ValueError as e:
            msg = str(e)
            detail = "invalid trace_id" if "invalid trace_id" in msg else msg
            raise HTTPException(status_code=400, detail=detail)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found")

    @app.delete("/api/runs/{trace_id}")
    def delete_run(
        trace_id: str, config: MaidaConfig = Depends(_get_config)
    ) -> Response:
        try:
            storage.delete_run(trace_id, config)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid trace_id")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found")
        return Response(status_code=204)

    @app.get("/favicon.svg")
    def serve_favicon() -> FileResponse:
        if not FAVICON_PATH.is_file():
            raise HTTPException(status_code=404, detail="favicon not found")
        return FileResponse(FAVICON_PATH, media_type="image/svg+xml")

    @app.get("/styles.css")
    def serve_styles() -> Response:
        if not UI_STYLES_PATH.is_file():
            raise HTTPException(status_code=404, detail="styles not found")
        response = FileResponse(UI_STYLES_PATH, media_type="text/css")
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/app.js")
    def serve_app_js() -> Response:
        if not UI_APP_JS_PATH.is_file():
            raise HTTPException(status_code=404, detail="app.js not found")
        response = FileResponse(UI_APP_JS_PATH, media_type="application/javascript")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def serve_ui() -> Response:
        if not UI_INDEX_PATH.is_file():
            raise HTTPException(
                status_code=404,
                detail="UI not found: maida/ui_static/index.html is missing",
            )
        response = FileResponse(UI_INDEX_PATH, media_type="text/html")
        response.headers["Cache-Control"] = "no-cache"
        return response

    return app
