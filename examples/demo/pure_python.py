"""
Pure python demo: produces a single run containing:
- RUN_START / RUN_END
- STATE_UPDATE
- TOOL_CALL (ok)
- LLM_CALL (ok)  [manual record, no network]
- LOOP_WARNING   [repeated TOOL_CALL pattern, m=1]
- TOOL_CALL (error)
- ERROR          [simulated exception; caught in __main__ so terminal stays clean]

Run from repo root:
   uv run python -m examples.demo.pure_python
Then:
  maida view
"""

import os

from maida import trace, record_llm_call, record_state, record_tool_call


def _ensure_demo_defaults() -> None:
    # Make loop detection predictable even if user has custom config.
    # If the user explicitly sets these env vars, we respect them.
    os.environ.setdefault("MAIDA_LOOP_WINDOW", "12")
    os.environ.setdefault("MAIDA_LOOP_REPETITIONS", "3")
    # Redaction is ON by default; leaving this alone keeps the demo honest.
    # os.environ.setdefault("MAIDA_REDACT", "1")


@trace(name="pure-python demo")
def run_demo() -> None:
    record_state(
        state={"phase": "start", "goal": "show behavioral trace evidence"},
        meta={"demo": "pure-python"},
    )

    # TOOL_CALL ok + redaction demo (api_key should become __REDACTED__ in trace)
    record_tool_call(
        name="lookup_customer",
        args={"customer_id": "cust_123", "api_key": "sk-demo-DO_NOT_USE"},
        result={"name": "Ada Lovelace", "plan": "pro"},
        meta={"demo": "pure-python", "step": "tool_ok"},
        status="ok",
    )

    # LLM_CALL ok (manual; no network)
    record_llm_call(
        model="demo-model-local",
        prompt="Summarize the customer record in one sentence.",
        response="Ada Lovelace is on the Pro plan.",
        usage={"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21},
        provider="local",
        temperature=0.0,
        stop_reason="stop",
        meta={"demo": "pure-python", "step": "llm_ok"},
        status="ok",
        error=None,
    )

    record_state(
        state={
            "phase": "loop-demo",
            "note": "repeat the same tool call to trigger LOOP_WARNING",
        },
        meta={"demo": "pure-python"},
    )

    # LOOP_WARNING demo: repeated identical TOOL_CALL signature back-to-back.
    # With repetitions=3, warning will appear by the 3rd call (and dedupe prevents spamming).
    for i in range(6):
        record_tool_call(
            name="search_docs",
            args={"query": "billing limits", "iteration": i},
            result={"hits": ["limits.md", "pricing.md"], "top": "limits.md"},
            meta={"demo": "pure-python", "step": "loop", "i": i},
            status="ok",
        )

    # TOOL_CALL error (clear status/error fields)
    record_tool_call(
        name="send_email",
        args={
            "to": "user@example.com",
            "subject": "Demo",
            "authorization": "Bearer demo-token-DO_NOT_USE",
        },
        result=None,
        meta={"demo": "pure-python", "step": "tool_error"},
        status="error",
        error="SMTP 550: mailbox unavailable",
    )

    # ERROR event demo: raise after we've recorded the interesting stuff.
    raise RuntimeError("Demo exception: simulated crash after tool failure")


if __name__ == "__main__":
    _ensure_demo_defaults()
    try:
        run_demo()
    except RuntimeError as e:
        # The trace has already been finalized with ERROR + RUN_END(status=error).
        # Keep terminal output clean for the demo recording.
        print(f"[demo] run completed with simulated error: {e}")
        print("[demo] open the timeline with: maida view")
