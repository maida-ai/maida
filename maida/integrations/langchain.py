"""
LangChain/LangGraph callback handler that forwards LLM and tool events to Maida.

Uses only Maida SDK: record_llm_call, record_tool_call. No CLI or server imports.
"""

import json
from typing import Any

from maida.exceptions import GuardrailExceeded, _MaidaAbortSignal
from maida.tracing import record_llm_call, record_tool_call
from maida.integrations._error import MissingOptionalDependencyError


try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError as e:
    raise MissingOptionalDependencyError(
        "The LangChain/LangGraph integration requires optional dependencies that are not installed. "
        "Missing: langchain (langchain_core). "
        'Install with: pip install "maida-ai[langchain]" or pip install langchain. '
        "This integration is optional; the core maida package does not depend on it."
    ) from e


def _model_from_serialized(serialized: dict[str, Any]) -> str:
    """Extract a model name from LangChain serialized LLM config; robust to missing fields."""
    if not serialized:
        return "unknown"
    ids = serialized.get("id")
    if isinstance(ids, (list, tuple)) and ids:
        return str(ids[-1]) if ids else "unknown"
    if isinstance(ids, str):
        return ids
    return str(serialized.get("name", "unknown"))


def _tool_name_from_serialized(serialized: dict[str, Any]) -> str:
    """Extract tool name from serialized tool config."""
    if not serialized:
        return "unknown"
    name = serialized.get("name")
    if isinstance(name, str):
        return name
    return str(serialized.get("id", "unknown"))


def _prompt_from_prompts(prompts: Any) -> Any:
    """Normalize prompts to a single value for record_llm_call (string or list)."""
    if prompts is None:
        return None
    if isinstance(prompts, (list, tuple)):
        return prompts[0] if len(prompts) == 1 else prompts
    return prompts


def _messages_as_prompt(messages: Any) -> Any:
    """Convert chat model messages to a JSON-serializable structure for prompt."""
    if messages is None:
        return None
    if not isinstance(messages, (list, tuple)):
        return messages
    out = []
    for inner in messages:
        if not isinstance(inner, (list, tuple)):
            continue
        for msg in inner:
            if hasattr(msg, "content") and hasattr(msg, "type"):
                out.append(
                    {"type": getattr(msg, "type", "unknown"), "content": msg.content}
                )
            else:
                out.append(str(msg))
    return out if out else None


def _response_from_llm_result(response: Any) -> tuple[Any, Any]:
    """Extract (response_text, usage_dict) from LLMResult. Returns (None, None) if not LLMResult."""
    if response is None:
        return None, None
    if getattr(response, "generations", None) is None:
        return None, None
    generations = response.generations
    if not generations:
        return None, None
    # First generation list, first generation's text
    first = generations[0]
    texts = []
    for g in first:
        if getattr(g, "text", None) is not None:
            texts.append(g.text)
    response_text = texts[0] if len(texts) == 1 else (texts if texts else None)
    usage = None
    if getattr(response, "llm_output", None) and isinstance(response.llm_output, dict):
        u = response.llm_output.get("token_usage") or response.llm_output
        if isinstance(u, dict):
            usage = {
                "prompt_tokens": u.get("prompt_tokens"),
                "completion_tokens": u.get("completion_tokens"),
                "total_tokens": u.get("total_tokens"),
            }
    return response_text, usage


class LangChainCallbackHandler(BaseCallbackHandler):
    """
    LangChain callback handler that records LLM and tool calls to the active
    Maida run via record_llm_call and record_tool_call.

    When a guardrail fires (e.g. stop_on_loop), the handler sets raise_error=True
    and re-raises, which tells LangChain to propagate the exception instead of
    swallowing it. This stops the graph mid-execution.

    As a defensive fallback, the exception is also stored on abort_exception
    so callers can check raise_if_aborted() after invoke() if needed.
    """

    def __init__(self) -> None:
        super().__init__()
        self._pending_llm: dict[str, dict[str, Any]] = {}
        self._pending_tool: dict[str, dict[str, Any]] = {}
        self._key_counter = 0
        self._abort_exception: GuardrailExceeded | None = None

    @property
    def abort_exception(self) -> GuardrailExceeded | None:
        """The guardrail exception if one fired during the last run, or None."""
        return self._abort_exception

    def raise_if_aborted(self) -> None:
        """Re-raise the guardrail exception if one was captured during the last run."""
        if self._abort_exception is not None:
            raise self._abort_exception

    def reset(self) -> None:
        """Clear abort state so the handler can be reused for a new run."""
        self._abort_exception = None
        self.raise_error = False

    def _key(self, run_id: Any = None, parent_run_id: Any = None, **kwargs: Any) -> str:
        """Composite key for pending calls; reduces collisions and handles missing run_id."""
        if run_id is not None:
            return f"{run_id}:{parent_run_id or ''}"
        if parent_run_id is not None:
            return f"parent:{parent_run_id}"
        self._key_counter += 1
        return f"fallback:{self._key_counter}"

    def _check_aborted(self) -> None:
        """If a guardrail abort was already triggered, raise
        _MaidaAbortSignal (BaseException) so it bypasses framework-level
        ``except Exception`` handlers (e.g. LangGraph's graph executor) and
        propagates all the way out to the traced_run context manager."""
        if self._abort_exception is not None:
            self.raise_error = True
            raise _MaidaAbortSignal(self._abort_exception)

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        self._check_aborted()
        key = self._key(run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        self._pending_llm[key] = {
            "model": _model_from_serialized(serialized),
            "prompt": _prompt_from_prompts(prompts),
        }

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[Any],
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        self._check_aborted()
        key = self._key(run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        self._pending_llm[key] = {
            "model": _model_from_serialized(serialized),
            "prompt": _messages_as_prompt(messages),
        }

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        key = self._key(run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        pending = self._pending_llm.pop(key, None)
        model = (pending or {}).get("model") or "unknown"
        prompt = (pending or {}).get("prompt") if pending else None
        response_text, usage = _response_from_llm_result(response)
        try:
            record_llm_call(
                model=model or "unknown",
                prompt=prompt,
                response=response_text,
                usage=usage,
                provider="langchain",
            )
        except GuardrailExceeded as e:
            self._abort_exception = e
            self.raise_error = True
            raise _MaidaAbortSignal(e) from e

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        key = self._key(run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        pending = self._pending_llm.pop(key, None)
        model = (pending or {}).get("model") or "unknown"
        prompt = (pending or {}).get("prompt") if pending else None
        try:
            record_llm_call(
                model=model,
                prompt=prompt,
                response=None,
                usage=None,
                status="error",
                error=error,
                provider="langchain",
            )
        except GuardrailExceeded as e:
            self._abort_exception = e
            self.raise_error = True
            raise _MaidaAbortSignal(e) from e

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        self._check_aborted()
        key = self._key(run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        name = _tool_name_from_serialized(serialized)
        try:
            args = json.loads(input_str) if input_str.strip() else {}
        except Exception:
            args = input_str if input_str else None
        self._pending_tool[key] = {"name": name, "args": args}

    def on_tool_end(
        self,
        output: str,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        key = self._key(run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        pending = self._pending_tool.pop(key, None)
        name = (pending or {}).get("name", "unknown")
        args = (pending or {}).get("args") if pending else None
        try:
            record_tool_call(name=name, args=args, result=output, status="ok")
        except GuardrailExceeded as e:
            self._abort_exception = e
            self.raise_error = True
            raise _MaidaAbortSignal(e) from e

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        key = self._key(run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        pending = self._pending_tool.pop(key, None)
        name = (pending or {}).get("name", "unknown")
        args = (pending or {}).get("args") if pending else None
        try:
            record_tool_call(
                name=name, args=args, result=None, status="error", error=error
            )
        except GuardrailExceeded as e:
            self._abort_exception = e
            self.raise_error = True
            raise _MaidaAbortSignal(e) from e
