"""
Viewer server tests: run_id validation and path-traversal hardening.

Uses tmp dir via MAIDA_DATA_DIR; no real home directory touched.
"""

import json

from fastapi.testclient import TestClient

from maida.config import load_config
from maida.server import create_app
from maida import storage


def _write_run(config, trace_id, run_name="test"):
    """Write a minimal run with meta.json + spans.jsonl for testing."""
    runs_base = config.data_dir / "runs"
    run_dir = runs_base / trace_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "trace_id": trace_id,
        "run_name": run_name,
        "started_at": "2026-01-01T12:00:00.000Z",
        "ended_at": "2026-01-01T12:00:01.000Z",
        "duration_ms": 1000,
        "status": "ok",
        "counts": {"llm_calls": 0, "tool_calls": 0, "errors": 0, "loop_warnings": 0},
    }
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    root_span = {
        "trace_id": trace_id,
        "span_id": "0" * 16,
        "parent_span_id": None,
        "name": run_name,
        "kind": "INTERNAL",
        "start_time": "2026-01-01T12:00:00.000Z",
        "end_time": "2026-01-01T12:00:01.000Z",
        "duration_ms": 1000,
        "attributes": {"maida.run_name": run_name},
        "events": [],
        "status_code": "OK",
        "status_description": "",
    }
    (run_dir / "spans.jsonl").write_text(json.dumps(root_span) + "\n", encoding="utf-8")


def _write_run_with_malformed_span(config, trace_id):
    runs_base = config.data_dir / "runs"
    run_dir = runs_base / trace_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "trace_id": trace_id,
        "run_name": "bad",
        "started_at": "2026-01-01T12:00:00.000Z",
        "ended_at": "2026-01-01T12:00:01.000Z",
        "duration_ms": 1000,
        "status": "ok",
        "counts": {"llm_calls": 0, "tool_calls": 0, "errors": 0, "loop_warnings": 0},
    }
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (run_dir / "spans.jsonl").write_text(
        '{"token":"sk-test-DO-NOT-LEAK",\n',
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Validator: reject path traversal and invalid format
# ---------------------------------------------------------------------------

# _validate_trace_id is private; test it through load_run_meta / server endpoints.


def test_run_id_accepts_valid_trace_id(temp_data_dir):
    """Validator accepts 32-char hex trace_id strings."""
    config = load_config()
    trace_id = "a" * 32
    _write_run(config, trace_id)
    meta = storage.load_run_meta(trace_id, config)
    assert meta["trace_id"] == trace_id


# ---------------------------------------------------------------------------
# Server: invalid run_id returns 400
# ---------------------------------------------------------------------------


def test_server_returns_400_for_invalid_run_id(temp_data_dir):
    """GET /api/runs/{run_id} with invalid run_id returns 400."""
    client = TestClient(create_app())
    r = client.get("/api/runs/short")
    assert r.status_code == 400
    assert "invalid" in (r.json().get("detail") or "").lower()


def test_server_returns_400_for_invalid_run_id_spans(temp_data_dir):
    """GET /api/runs/{run_id}/spans with invalid run_id returns 400."""
    client = TestClient(create_app())
    r = client.get("/api/runs/short/spans")
    assert r.status_code == 400, r.json()
    assert "invalid" in (r.json().get("detail") or "").lower()


def test_server_spans_returns_422_for_invalid_run_format(temp_data_dir):
    config = load_config()
    trace_id = "abcabc20" + "a" * 24
    _write_run_with_malformed_span(config, trace_id)
    client = TestClient(create_app())

    r = client.get(f"/api/runs/{trace_id}/spans")

    assert r.status_code == 422
    detail = r.json().get("detail") or ""
    assert "spans.jsonl line 1" in detail
    assert "Next step:" in detail
    assert "sk-test-DO-NOT-LEAK" not in detail


def test_server_returns_404_for_valid_but_missing_run_id(temp_data_dir):
    """Valid trace_id format but missing run directory returns 404."""
    client = TestClient(create_app())
    r = client.get("/api/runs/" + "b" * 32)
    assert r.status_code == 404
    assert "not found" in (r.json().get("detail") or "").lower()


def test_server_returns_200_for_valid_run_id(temp_data_dir):
    """Valid trace_id with existing run returns 200 and metadata."""
    config = load_config()
    trace_id = "c" * 32
    _write_run(config, trace_id)
    client = TestClient(create_app())
    r = client.get(f"/api/runs/{trace_id}")
    assert r.status_code == 200
    assert r.json().get("trace_id") == trace_id


def test_server_paths_endpoint_returns_run_json_path(temp_data_dir):
    """GET /api/runs/{run_id}/paths returns local file paths."""
    config = load_config()
    trace_id = "d" * 32
    _write_run(config, trace_id)
    client = TestClient(create_app())

    r = client.get(f"/api/runs/{trace_id}/paths")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("trace_id") == trace_id
    paths = data.get("paths") or {}
    assert "meta_json" in paths


def test_server_paths_endpoint_invalid_and_missing_run_id(temp_data_dir):
    """Paths endpoint mirrors run meta semantics for invalid/missing IDs."""
    client = TestClient(create_app())

    r = client.get("/api/runs/short/paths")
    assert r.status_code == 400
    assert "invalid" in (r.json().get("detail") or "").lower()

    r2 = client.get("/api/runs/" + "e" * 32 + "/paths")
    assert r2.status_code == 404
    assert "not found" in (r2.json().get("detail") or "").lower()


def test_server_can_rename_run(temp_data_dir):
    """POST /api/runs/{run_id}/rename updates meta.json run_name on disk."""
    config = load_config()
    trace_id = "f" * 32
    _write_run(config, trace_id, "before")
    client = TestClient(create_app())

    r = client.post(f"/api/runs/{trace_id}/rename", json={"run_name": "after"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("run_name") == "after"

    reloaded = storage.load_run_meta(trace_id, config)
    assert reloaded["run_name"] == "after"


def test_server_rename_invalid_and_missing_run_id(temp_data_dir):
    """Rename endpoint validates run_id and missing runs."""
    client = TestClient(create_app())

    r = client.post("/api/runs/short/rename", json={"run_name": "x"})
    assert r.status_code == 400
    assert "invalid" in (r.json().get("detail") or "").lower()

    r2 = client.post(
        "/api/runs/" + "a" * 32 + "/rename",
        json={"run_name": "x"},
    )
    assert r2.status_code == 404
    assert "not found" in (r2.json().get("detail") or "").lower()


def test_server_can_delete_run(temp_data_dir):
    """DELETE /api/runs/{run_id} removes the run directory."""
    config = load_config()
    trace_id = "a" * 32
    _write_run(config, trace_id)
    run_dir = config.data_dir / "runs" / trace_id
    assert run_dir.is_dir()

    client = TestClient(create_app())

    r = client.delete(f"/api/runs/{trace_id}")
    assert r.status_code == 204, r.text

    assert not run_dir.exists()


def test_server_delete_invalid_and_missing_run_id(temp_data_dir):
    """DELETE endpoint validates run_id and missing runs."""
    client = TestClient(create_app())

    r = client.delete("/api/runs/short")
    assert r.status_code == 400
    assert "invalid" in (r.json().get("detail") or "").lower()

    r2 = client.delete("/api/runs/" + "a" * 32)
    assert r2.status_code == 404
    assert "not found" in (r2.json().get("detail") or "").lower()
