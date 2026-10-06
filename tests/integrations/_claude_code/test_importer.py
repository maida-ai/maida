"""Offline claude_code importer tests."""

from __future__ import annotations
from tests.support.paths import FIXTURES_ROOT
import json
import pytest
from maida.config import load_config
from maida.constants import SPEC_VERSION
from maida.integrations.claude_code import (
    ClaudeCaptureChangedError,
    import_claude_capture,
    load_capture_segment,
    normalize_claude_capture,
)
from maida.storage import install_validated_run, load_validated_run
from tests.support.claude_code import _install_fixture


FIXTURES = FIXTURES_ROOT / "traces" / "claude-code" / "2.1.220"

SESSIONS = {
    "normal": "fixture-normal",
    "regression": "fixture-regression",
    "log-only": "fixture-log-only",
    "malformed": "fixture-malformed",
}


def test_import_refuses_to_silently_reuse_previous_mapping(temp_data_dir):
    _install_fixture("normal", temp_data_dir)
    config = load_config()
    normalized = normalize_claude_capture(load_capture_segment(FIXTURES / "normal"), config)
    root = normalized.spans[0]
    source = json.loads(root["attributes"]["maida.meta"])
    source["claude_code"]["mapping_version"] = 1
    root["attributes"]["maida.meta"] = json.dumps(source)
    install_validated_run(normalized.meta, normalized.spans, config)

    with pytest.raises(ClaudeCaptureChangedError, match="refusing to overwrite"):
        import_claude_capture("fixture-normal", config)


def test_import_is_atomic_idempotent_and_refuses_changed_source(temp_data_dir):
    capture_dir = _install_fixture("normal", temp_data_dir)
    config = load_config()

    first = import_claude_capture("fixture-normal", config)
    second = import_claude_capture("fixture-normal", config)
    assert first.imported is True
    assert second.imported is False
    assert first.trace_id == second.trace_id
    meta, spans = load_validated_run(first.trace_id, config)
    assert meta["spec_version"] == SPEC_VERSION
    assert spans

    with (capture_dir / "logs.jsonl").open("a", encoding="utf-8") as stream:
        changed = json.loads((FIXTURES / "normal" / "logs.jsonl").read_text().splitlines()[-1])
        changed["record"]["attributes"]["event.sequence"] = 99
        stream.write(json.dumps(changed) + "\n")
    manifest = json.loads((capture_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["signals"]["logs"] = 5
    (capture_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ClaudeCaptureChangedError, match="changed"):
        import_claude_capture("fixture-normal", config)
