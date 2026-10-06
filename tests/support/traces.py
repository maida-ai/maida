"""Reusable test helpers."""

import json
import shutil
from pathlib import Path
from tests.support.paths import FIXTURES_ROOT


FIXTURES = FIXTURES_ROOT / "traces/current"


def _copy_trace(
    fixture: str,
    runs_dir: Path,
    *,
    trace_id: str,
    run_name: str | None,
    started_at: str,
) -> Path:
    destination = runs_dir / trace_id
    shutil.copytree(FIXTURES / fixture, destination)

    meta_path = destination / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    old_trace_id = meta["trace_id"]
    meta.update(
        trace_id=trace_id,
        run_name=run_name,
        started_at=started_at,
    )
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    spans_path = destination / "spans.jsonl"
    spans = []
    for line in spans_path.read_text(encoding="utf-8").splitlines():
        span = json.loads(line)
        assert span["trace_id"] == old_trace_id
        span["trace_id"] = trace_id
        spans.append(span)
    spans_path.write_text("".join(json.dumps(span) + "\n" for span in spans), encoding="utf-8")
    return destination
