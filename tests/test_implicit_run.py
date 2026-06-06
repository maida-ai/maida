"""
Tests for implicit run: when MAIDA_IMPLICIT_RUN=1, record_* without @trace
creates a run and writes RUN_START; atexit writes RUN_END.
Runs the recorder in a subprocess so atexit finalization happens and test process stays clean.

TODO: If we drop implicit run later, we should remove this test.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from maida.config import load_config
from maida.events import EventType, spans_to_events
from maida.storage import load_run_meta, list_runs, load_spans


def _run_implicit_tool_call(data_dir: str) -> None:
    """Subprocess: set env, call record_tool_call with no trace, exit (atexit finalizes)."""
    env = os.environ.copy()
    env["MAIDA_IMPLICIT_RUN"] = "1"
    env["MAIDA_DATA_DIR"] = data_dir
    code = """
from maida.tracing import record_tool_call
record_tool_call("no_trace_tool", args={"x": 1})
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=os.getcwd(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_implicit_run_creates_run_with_run_start_and_tool_call():
    """With MAIDA_IMPLICIT_RUN=1, record_tool_call (no @trace) creates run with RUN_START and TOOL_CALL."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = str(Path(tmp).resolve())
        _run_implicit_tool_call(data_dir)

        os.environ["MAIDA_DATA_DIR"] = data_dir
        try:
            config = load_config()
            runs = list_runs(limit=5, config=config)
            assert len(runs) >= 1, "expected at least one run"
            implicit = next((r for r in runs if r.get("run_name") == "implicit"), None)
            assert implicit is not None, "expected a run with run_name=='implicit'"
            run_id = implicit.get("run_id") or implicit.get("trace_id")

            events = spans_to_events(load_spans(run_id, config))
            event_types = [e.get("event_type") for e in events]

            assert EventType.RUN_START.value in event_types, "expected RUN_START"
            assert EventType.TOOL_CALL.value in event_types, "expected TOOL_CALL"

            run_json = load_run_meta(run_id, config)
            assert run_json["status"] in ("ok", "running"), (
                "run should be finalized or still running"
            )
            assert run_json["counts"]["tool_calls"] == 1
        finally:
            os.environ.pop("MAIDA_DATA_DIR", None)
