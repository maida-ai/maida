"""Span attribute names shared by OTel export, event mapping, and importers.

Leaf module: no imports from other ``maida._tracing`` modules, so callers can
depend on these names without loading tracer setup or run context.
"""

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
