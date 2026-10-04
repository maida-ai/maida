"""
Minimal FastAPI server for the local viewer.

Serves trace (run) metadata and spans via OTel-based storage.
GET /api/runs, GET /api/runs/{trace_id}, GET /api/runs/{trace_id}/spans,
and GET / with static index.html. UI assets are served from /static/.
"""

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import maida.storage as storage
from maida.config import MaidaConfig, load_config
from maida.constants import SPEC_VERSION
from maida.events import spans_to_events

UI_STATIC_DIR = Path(__file__).resolve().parent / "ui_static"
UI_INDEX_PATH = UI_STATIC_DIR / "index.html"
UI_BRAND_TOKENS_PATH = UI_STATIC_DIR / "brand-tokens.css"
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


def create_app(config: MaidaConfig | None = None) -> FastAPI:
    # Viewer requests must not become agent runs in Maida's global OTel provider.
    app = FastAPI(
        title="Maida Viewer",
        telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False},
    )
    app.state.config = config if config is not None else load_config()

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
            # TODO: cache or incrementally project events for large live traces.
            meta, _ = storage.load_validated_run(trace_id, config)
            return meta
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid trace_id")
        except storage.RunValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/api/runs/{trace_id}/spans")
    def get_run_spans(trace_id: str, config: MaidaConfig = Depends(_get_config)) -> dict:
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
    def get_run_paths(trace_id: str, config: MaidaConfig = Depends(_get_config)) -> dict:
        try:
            paths = storage.get_run_paths(trace_id, config)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid trace_id")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found")
        return {"spec_version": SPEC_VERSION, "trace_id": trace_id, "paths": paths}

    @app.get("/api/runs/{trace_id}/rename")
    def validate_run_for_rename(trace_id: str, config: MaidaConfig = Depends(_get_config)) -> dict:
        trace_id = _validated_trace_id(trace_id)
        try:
            # TODO: cache or incrementally project events for large live traces.
            meta, _ = storage.load_validated_run(trace_id, config)
            return meta
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid trace_id")
        except storage.RunValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.post("/api/runs/{trace_id}/rename")
    def rename_run(
        trace_id: str,
        payload: RenameRunRequest,
        config: MaidaConfig = Depends(_get_config),
    ) -> dict:
        trace_id = _validated_trace_id(trace_id)
        try:
            storage.load_validated_run(trace_id, config)
            return storage.rename_run(trace_id, payload.run_name, config)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid trace_id")
        except storage.RunValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.delete("/api/runs/{trace_id}")
    def delete_run(trace_id: str, config: MaidaConfig = Depends(_get_config)) -> Response:
        try:
            storage.delete_run(trace_id, config)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid trace_id")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found")
        return Response(status_code=204)

    @app.get("/")
    def serve_ui() -> Response:
        if not UI_INDEX_PATH.is_file():
            raise HTTPException(
                status_code=404,
                detail="UI not found: maida/ui_static/index.html is missing",
            )
        return FileResponse(UI_INDEX_PATH, media_type="text/html")

    # After API routes: directory-scoped static assets (path-safe; no per-file routes).
    app.mount("/static", StaticFiles(directory=UI_STATIC_DIR), name="ui")

    return app
