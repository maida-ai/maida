"""Read-only claude_code import: importer."""

from __future__ import annotations

import json

from maida._tracing.attributes import (
    MAIDA_META,
)
from maida.config import MaidaConfig
from maida.integrations._claude_code.loading import load_claude_capture
from maida.integrations._claude_code.normalize import normalize_claude_capture
from maida.integrations._claude_code.types import (
    _MAPPING_VERSION,
    ClaudeCaptureChangedError,
    ClaudeCaptureImportError,
    ClaudeImportResult,
    NormalizedClaudeRun,
)
from maida.onboarding import record_completed_capture
from maida.storage import install_validated_run, load_validated_run


def _existing_matches(run: NormalizedClaudeRun, config: MaidaConfig) -> bool:
    try:
        _meta, spans = load_validated_run(run.trace_id, config)
    except FileNotFoundError:
        return False
    except Exception as exc:
        raise ClaudeCaptureImportError(f"existing destination run {run.trace_id} is invalid") from exc
    root = next((span for span in spans if span.get("parent_span_id") is None), None)
    if root is None:
        raise ClaudeCaptureImportError(f"existing destination run {run.trace_id} has no root span")
    try:
        source = json.loads(root["attributes"][MAIDA_META])["claude_code"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ClaudeCaptureImportError(f"existing destination run {run.trace_id} is not a Claude import") from exc
    if source.get("session_hash") != run.session_hash or source.get("segment") != run.segment:
        raise ClaudeCaptureImportError(f"deterministic trace ID collision at destination {run.trace_id}")
    if source.get("mapping_version") != _MAPPING_VERSION or source.get("source_fingerprint") != run.source_fingerprint:
        raise ClaudeCaptureChangedError(
            f"Claude Code capture changed since destination {run.trace_id} was imported; "
            "refusing to overwrite the local run"
        )
    return True


def import_claude_capture(
    session_id: str,
    config: MaidaConfig,
    *,
    segment: str = "latest",
) -> ClaudeImportResult:
    """Load, normalize, and atomically install one Claude capture."""
    capture = load_claude_capture(session_id, config, segment=segment)
    run = normalize_claude_capture(capture, config)
    if _existing_matches(run, config):
        imported = False
    else:
        try:
            install_validated_run(run.meta, run.spans, config)
        except FileExistsError as exc:
            raise ClaudeCaptureImportError(
                f"destination run {run.trace_id} was created concurrently; rerun import"
            ) from exc
        imported = True
    record_completed_capture(config, run.session_hash, run.segment)
    return ClaudeImportResult(
        trace_id=run.trace_id,
        run_name=run.run_name,
        session_hash=run.session_hash,
        segment=run.segment,
        source_fingerprint=run.source_fingerprint,
        imported=imported,
    )
