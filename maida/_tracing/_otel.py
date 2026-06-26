"""
OpenTelemetry infrastructure for Maida: tracer provider, span processors, exporters.

Provides:
- _setup_otel(): Initialize TracerProvider with local file + optional OTLP export.
- _get_tracer(): Get the Maida tracer.
- GenAI semantic convention constant names.
- span_to_dict(): Serialize a ReadableSpan to a JSON-safe dict for local storage.
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from opentelemetry import trace
from opentelemetry.trace import SpanKind
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExportResult,
    SpanExporter,
)

from maida._tracing._redact import _redact_and_truncate
from maida.config import MaidaConfig, load_config
from maida.constants import SPEC_VERSION

_MAIDA_TRACER_NAME = "maida"
_MAIDA_TRACER_VERSION = "0.1.0"

_tracer_provider: Any | None = None
_tracer: trace.Tracer | None = None
_lock = threading.Lock()

# GenAI semantic convention attribute names (stable as of spec v1.41+)
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
GEN_AI_RESPONSE_ID = "gen_ai.response.id"

# Maida-specific attribute names
MAIDA_RUN_NAME = "maida.run_name"
MAIDA_PYTHON_VERSION = "maida.python_version"
MAIDA_PLATFORM = "maida.platform"
MAIDA_CWD = "maida.cwd"
MAIDA_ARGV = "maida.argv"
MAIDA_TOOL_NAME = "maida.tool_name"
MAIDA_TOOL_ARGS = "maida.tool.args"
MAIDA_TOOL_RESULT = "maida.tool.result"
MAIDA_STATUS = "maida.status"
MAIDA_ERROR_TYPE = "maida.error_type"
MAIDA_ERROR_MESSAGE = "maida.error_message"
MAIDA_ERROR_STACK = "maida.error_stack"
MAIDA_LLM_COUNT = "maida.llm_calls"
MAIDA_TOOL_COUNT = "maida.tool_calls"
MAIDA_ERROR_COUNT = "maida.errors"
MAIDA_LOOP_WARNING_COUNT = "maida.loop_warnings"
MAIDA_META = "maida.meta"
MAIDA_EVENT_TYPE = "maida.event_type"

_JSON_STRING_ATTRIBUTE_KEYS = frozenset({MAIDA_META})
_JSON_STRING_EVENT_ATTRIBUTE_KEYS = frozenset(
    {
        "args",
        "result",
        "state",
        "diff",
        MAIDA_TOOL_ARGS,
        MAIDA_TOOL_RESULT,
    }
)
_NON_SECRET_ATTRIBUTE_KEYS = frozenset(
    {
        GEN_AI_USAGE_INPUT_TOKENS,
        GEN_AI_USAGE_OUTPUT_TOKENS,
        GEN_AI_USAGE_TOTAL_TOKENS,
    }
)


def span_kind_str(kind: SpanKind) -> str:
    mapping = {
        SpanKind.INTERNAL: "INTERNAL",
        SpanKind.CLIENT: "CLIENT",
        SpanKind.SERVER: "SERVER",
        SpanKind.PRODUCER: "PRODUCER",
        SpanKind.CONSUMER: "CONSUMER",
    }
    return mapping.get(kind, "INTERNAL")


def nanos_to_iso(nanos: int) -> str:
    """Convert nanosecond timestamp to ISO8601 UTC with microsecond precision and trailing Z."""
    dt = datetime.fromtimestamp(nanos / 1_000_000_000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def nanos_to_ms(nanos: int) -> int:
    return max(0, int(nanos / 1_000_000))


def _decode_attribute_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def _sanitize_attribute_mapping(
    attributes: Any,
    config: MaidaConfig,
    *,
    json_string_keys: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    if not attributes:
        return safe

    for raw_key, raw_value in attributes.items():
        key = str(raw_key)
        value = _decode_attribute_value(raw_value)
        if key in _NON_SECRET_ATTRIBUTE_KEYS:
            safe[key] = _redact_and_truncate(value, config)
            continue
        if key in json_string_keys and isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                pass
            else:
                safe[key] = json.dumps(
                    _redact_and_truncate(parsed, config),
                    ensure_ascii=False,
                    default=str,
                )
                continue

        redacted = _redact_and_truncate({key: value}, config)
        if isinstance(redacted, dict) and key in redacted:
            safe[key] = redacted[key]
        else:
            safe[key] = _redact_and_truncate(value, config)
    return safe


def span_to_dict(
    span: ReadableSpan, config: MaidaConfig | None = None
) -> dict[str, Any]:
    """Serialize a ReadableSpan to a JSON-safe dict for local storage."""
    config = config or load_config()
    sc = span.context
    parent_sc = span.parent
    start_nanos = span.start_time or 0
    end_nanos = span.end_time or 0
    duration_ms = (
        nanos_to_ms(end_nanos - start_nanos) if end_nanos > start_nanos else None
    )

    attrs = _sanitize_attribute_mapping(
        span.attributes,
        config,
        json_string_keys=_JSON_STRING_ATTRIBUTE_KEYS,
    )

    events_list: list[dict[str, Any]] = []
    for ev in span.events or []:
        ev_attrs = _sanitize_attribute_mapping(
            ev.attributes,
            config,
            json_string_keys=_JSON_STRING_EVENT_ATTRIBUTE_KEYS,
        )
        events_list.append(
            {
                "name": ev.name,
                "timestamp": nanos_to_iso(ev.timestamp),
                "attributes": ev_attrs,
            }
        )

    status_code = "UNSET"
    status_desc = ""
    if span.status:
        status_code = (
            span.status.status_code.name
            if hasattr(span.status.status_code, "name")
            else str(span.status.status_code)
        )
        status_desc = span.status.description or ""
    status_desc = _redact_and_truncate(status_desc, config)

    return {
        "spec_version": SPEC_VERSION,
        "trace_id": format(sc.trace_id, "032x"),
        "span_id": format(sc.span_id, "016x"),
        "parent_span_id": format(parent_sc.span_id, "016x") if parent_sc else None,
        "name": span.name or "",
        "kind": span_kind_str(span.kind) if span.kind else "INTERNAL",
        "start_time": nanos_to_iso(start_nanos),
        "end_time": nanos_to_iso(end_nanos) if end_nanos else None,
        "duration_ms": duration_ms,
        "attributes": attrs,
        "events": events_list,
        "status_code": status_code,
        "status_description": status_desc,
    }


class MaidaLocalSpanExporter(SpanExporter):
    """Exports completed spans to local filesystem organized by trace_id.

    Each trace (run) gets a directory: <data_dir>/runs/<trace_id>/
      - meta.json: run metadata (aggregated from root span)
      - spans.jsonl: one JSON span dict per line
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        config = load_config()
        self._config = config
        self._runs_dir = (data_dir or config.data_dir).expanduser() / "runs"
        self._lock = threading.Lock()

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        try:
            for span in spans:
                sc = span.context
                if not sc:
                    continue
                trace_id_hex = format(sc.trace_id, "032x")
                run_dir = self._runs_dir / trace_id_hex
                run_dir.mkdir(parents=True, exist_ok=True)

                span_dict = span_to_dict(span, self._config)

                spans_path = run_dir / "spans.jsonl"
                with self._lock:
                    with open(spans_path, "a", encoding="utf-8") as f:
                        f.write(
                            json.dumps(span_dict, ensure_ascii=False, default=str)
                            + "\n"
                        )
                        f.flush()
                        os.fsync(f.fileno())

                if not span.parent:
                    self._update_meta(run_dir, span_dict, span)
                else:
                    self._ensure_running_meta(run_dir, span_dict)
            return SpanExportResult.SUCCESS
        except Exception:
            return SpanExportResult.FAILURE

    def _write_meta(self, meta_path: Path, meta: dict[str, Any]) -> None:
        tmp = meta_path.with_name(f".{meta_path.name}.{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, meta_path)

    def _ensure_running_meta(self, run_dir: Path, span_dict: dict) -> None:
        meta_path = run_dir / "meta.json"
        with self._lock:
            if meta_path.is_file():
                return
            self._write_meta(
                meta_path,
                {
                    "spec_version": SPEC_VERSION,
                    "trace_id": span_dict["trace_id"],
                    "run_name": None,
                    "started_at": span_dict.get("start_time"),
                    "ended_at": None,
                    "duration_ms": None,
                    "status": "running",
                    "counts": {
                        "llm_calls": 0,
                        "tool_calls": 0,
                        "errors": 0,
                        "loop_warnings": 0,
                    },
                },
            )

    def _update_meta(
        self, run_dir: Path, span_dict: dict, raw_span: ReadableSpan
    ) -> None:
        attrs = span_dict.get("attributes", {})
        meta_path = run_dir / "meta.json"
        status = "running"
        if span_dict.get("status_code") == "OK":
            status = "ok"
        elif span_dict.get("status_code") == "ERROR":
            status = "error"
        meta = {
            "spec_version": SPEC_VERSION,
            "trace_id": span_dict["trace_id"],
            "run_name": attrs.get(MAIDA_RUN_NAME),
            "started_at": span_dict.get("start_time"),
            "ended_at": span_dict.get("end_time"),
            "duration_ms": span_dict.get("duration_ms"),
            "status": status,
            "counts": {
                "llm_calls": attrs.get(MAIDA_LLM_COUNT, 0),
                "tool_calls": attrs.get(MAIDA_TOOL_COUNT, 0),
                "errors": attrs.get(MAIDA_ERROR_COUNT, 0),
                "loop_warnings": attrs.get(MAIDA_LOOP_WARNING_COUNT, 0),
            },
        }
        with self._lock:
            self._write_meta(meta_path, meta)

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _setup_otel() -> None:
    global _tracer_provider, _tracer
    with _lock:
        if _tracer_provider is not None:
            return
        current_provider = trace.get_tracer_provider()
        if hasattr(current_provider, "add_span_processor"):
            provider = current_provider
        else:
            provider = TracerProvider()
            trace.set_tracer_provider(provider)

        local_exporter = MaidaLocalSpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(local_exporter))

        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
                provider.add_span_processor(SimpleSpanProcessor(otlp_exporter))
            except Exception:
                pass

        _tracer_provider = provider
        _tracer = cast(
            trace.Tracer,
            provider.get_tracer(_MAIDA_TRACER_NAME, _MAIDA_TRACER_VERSION),
        )


def _get_tracer() -> trace.Tracer:
    if _tracer is None:
        _setup_otel()
    assert _tracer is not None
    return _tracer


def _shutdown_otel() -> None:
    global _tracer_provider, _tracer
    with _lock:
        if _tracer_provider is not None:
            try:
                _tracer_provider.shutdown()
            except Exception:
                pass
            _tracer_provider = None
            _tracer = None
    try:
        import opentelemetry.trace as _ot_trace

        _ot_trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
        _ot_trace._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    except Exception:
        pass


def has_active_run() -> bool:
    """Return True if there is an active span anywhere in the current context."""
    span = trace.get_current_span()
    return span is not None and span.is_recording()
