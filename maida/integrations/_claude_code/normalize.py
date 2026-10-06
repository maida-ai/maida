"""Normalize Claude evidence through explicit, ordered projection stages."""

from maida.config import MaidaConfig
from maida.integrations._claude_code.assembly import assemble_run
from maida.integrations._claude_code.context import NormalizationContext
from maida.integrations._claude_code.hook_tools import project_hook_tools
from maida.integrations._claude_code.log_projection import project_logs
from maida.integrations._claude_code.source_spans import project_source_spans
from maida.integrations._claude_code.types import ClaudeCaptureSegment, NormalizedClaudeRun


def normalize_claude_capture(capture: ClaudeCaptureSegment, config: MaidaConfig) -> NormalizedClaudeRun:
    """Project one capture segment into the current Maida trace schema."""
    context = NormalizationContext(capture, config)
    project_source_spans(context)
    project_hook_tools(context)
    project_logs(context)
    return assemble_run(context)
