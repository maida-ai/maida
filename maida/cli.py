"""Public CLI entrypoint; implementations live in maida._cli."""

import codecs
import json
import sys

import typer as typer
from typer import Exit as Exit

from maida._cli.app import (
    app as app,
)
from maida._cli.app import (
    capture_app as capture_app,
)
from maida._cli.app import (
    import_app as import_app,
)
from maida._cli.app import (
    scenario_app as scenario_app,
)
from maida._cli.capture import capture_claude_code_cmd as capture_claude_code_cmd
from maida._cli.capture import capture_claude_hook_cmd as capture_claude_hook_cmd
from maida._cli.capture import capture_codex_hook_cmd as capture_codex_hook_cmd
from maida._cli.capture import import_claude_code_cmd as import_claude_code_cmd
from maida._cli.capture import import_langfuse_cmd as import_langfuse_cmd
from maida._cli.common import _DEMO_TRACE_DURATION_MS as _DEMO_TRACE_DURATION_MS
from maida._cli.common import _PLAN_BACKEND_INSTALL_COMMAND as _PLAN_BACKEND_INSTALL_COMMAND
from maida._cli.common import EXIT_INTERNAL as EXIT_INTERNAL
from maida._cli.common import EXIT_NOT_FOUND as EXIT_NOT_FOUND
from maida._cli.common import _acceptance_source_from_environment as _acceptance_source_from_environment
from maida._cli.common import _emit_claude_capture_error as _emit_claude_capture_error
from maida._cli.common import _emit_langfuse_error as _emit_langfuse_error
from maida._cli.common import _exit_run_validation_error as _exit_run_validation_error
from maida._cli.common import _exit_unsupported_trace_format as _exit_unsupported_trace_format
from maida._cli.common import _read_config as _read_config
from maida._cli.common import _resolve_run_or_latest as _resolve_run_or_latest
from maida._cli.common import _trace_validation_payload as _trace_validation_payload
from maida._cli.common import validate_trace_cmd as validate_trace_cmd
from maida._cli.demo import _demo_generated_plan as _demo_generated_plan
from maida._cli.demo import _demo_regression as _demo_regression
from maida._cli.demo import _demo_single_run as _demo_single_run
from maida._cli.demo import _iso_add_ms as _iso_add_ms
from maida._cli.demo import _normalize_demo_trace_duration as _normalize_demo_trace_duration
from maida._cli.demo import demo_cmd as demo_cmd
from maida._cli.execution import drift_cmd as drift_cmd
from maida._cli.execution import extract_cmd as extract_cmd
from maida._cli.execution import run_cmd as run_cmd
from maida._cli.execution import scenario_run_cmd as scenario_run_cmd
from maida._cli.gating import accept_cmd as accept_cmd
from maida._cli.gating import assert_cmd as assert_cmd
from maida._cli.gating import baseline_cmd as baseline_cmd
from maida._cli.gating import check_cmd as check_cmd
from maida._cli.inspection import _format_text_table as _format_text_table
from maida._cli.inspection import _run_table_rows as _run_table_rows
from maida._cli.inspection import _wait_for_port as _wait_for_port
from maida._cli.inspection import diff_cmd as diff_cmd
from maida._cli.inspection import export_cmd as export_cmd
from maida._cli.inspection import list_cmd as list_cmd
from maida._cli.inspection import view_cmd as view_cmd
from maida._cli.setup import detach_cmd as detach_cmd
from maida._cli.setup import init_cmd as init_cmd
from maida._cli.version import _version_callback as _version_callback
from maida._cli.version import version_callback as version_callback


def main() -> None:
    """CLI entrypoint (console script maida.cli:main)."""

    # Preserve the caller's encoding, including redirected Windows streams,
    # while keeping report symbols from turning a verdict into an I/O failure.
    def escape_output(error):
        if not isinstance(error, UnicodeEncodeError):
            raise error
        # JSON uses surrogate pairs for non-BMP characters; backslashreplace
        # emits invalid JSON escapes such as \U0001f600 instead.
        return json.dumps(error.object[error.start : error.end], ensure_ascii=True)[1:-1], error.end

    codecs.register_error("maida_cli_escape", escape_output)
    for stream in (sys.stdout, sys.stderr):
        if getattr(stream, "errors", None) == "strict" and callable(getattr(stream, "reconfigure", None)):
            stream.reconfigure(errors="maida_cli_escape")
    app()


if __name__ == "__main__":
    main()
