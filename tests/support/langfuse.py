"""Offline langfuse observation and capture helpers."""

from __future__ import annotations
import json
from tests.support.paths import FIXTURES_ROOT
from typer.testing import CliRunner


runner = CliRunner()

FIXTURE_PATH = FIXTURES_ROOT / "langfuse" / "api-v2" / "observations.json"


def _observation(
    observation_id: str,
    *,
    trace_id: str = "source-trace-good",
    observation_type: str = "TOOL",
    name: str = "lookup",
    parent_id: str | None = None,
    start_second: int = 0,
    end_second: int | None = 1,
    level: str = "DEFAULT",
    input_value: object = None,
    output_value: object = None,
    metadata: object = None,
    usage: dict[str, int] | None = None,
) -> dict:
    return {
        "id": observation_id,
        "traceId": trace_id,
        "projectId": "project-public",
        "parentObservationId": parent_id,
        "type": observation_type,
        "name": name,
        "startTime": f"2026-01-01T00:00:{start_second:02d}.000Z",
        "endTime": (f"2026-01-01T00:00:{end_second:02d}.000Z" if end_second is not None else None),
        "level": level,
        "statusMessage": "source failure" if level == "ERROR" else None,
        "input": input_value,
        "output": output_value,
        "metadata": metadata or {},
        "providedModelName": "model-a" if observation_type == "GENERATION" else None,
        "modelParameters": {"temperature": 0},
        "usageDetails": usage or {},
        "costDetails": {"total": 0.001},
        "totalCost": 0.001,
        "traceName": "nightly-support-job",
        "sessionId": "session-public",
        "environment": "test",
        "release": "release-public",
        "tags": ["nightly"],
    }


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._payload
