"""Bundled demo agent: a simulated customer-support flow.

Everything is canned data recorded through the normal tracing API — no
network, no API keys, no LLM SDK. The run it produces is deterministic in
structure (event sequence, tool path, token counts), which makes it usable
as a baseline source for the regression demo.
"""

import os

from maida import record_llm_call, record_state, record_tool_call, traced_run

DEMO_RUN_NAME = "demo-support-agent"


def ensure_demo_env() -> None:
    """Make loop detection predictable even if the user has custom config.

    Explicitly set env vars are respected (``setdefault`` only).
    """
    os.environ.setdefault("MAIDA_LOOP_WINDOW", "12")
    os.environ.setdefault("MAIDA_LOOP_REPETITIONS", "3")


def run_good_agent() -> None:
    """Run the known-good version of the demo support agent."""
    with traced_run(name=DEMO_RUN_NAME):
        record_state(
            state={"phase": "triage", "ticket": "ORD-1042: where is my refund?"},
            meta={"demo": "support-agent"},
        )

        # api_key demonstrates redaction: it is scrubbed before hitting disk.
        record_tool_call(
            name="lookup_customer",
            args={"customer_id": "cust_1042", "api_key": "sk-demo-DO_NOT_USE"},
            result={"name": "Ada Lovelace", "plan": "pro", "open_orders": 1},
            meta={"demo": "support-agent"},
            status="ok",
        )

        record_tool_call(
            name="search_kb",
            args={"query": "refund policy pro plan"},
            result={"top": "refunds.md", "hits": ["refunds.md", "billing.md"]},
            meta={"demo": "support-agent"},
            status="ok",
        )

        record_llm_call(
            model="demo-gpt-4",
            prompt="Draft a reply about the refund timeline for a Pro customer.",
            response=(
                "Hi Ada, your refund for ORD-1042 was approved and will arrive "
                "within 5 business days."
            ),
            usage={"prompt_tokens": 64, "completion_tokens": 26, "total_tokens": 90},
            provider="local",
            temperature=0.0,
            stop_reason="stop",
            meta={"demo": "support-agent"},
            status="ok",
        )

        record_tool_call(
            name="send_reply",
            args={"ticket_id": "ORD-1042", "channel": "email"},
            result={"delivered": True},
            meta={"demo": "support-agent"},
            status="ok",
        )

        record_state(
            state={"phase": "done", "resolution": "answered"},
            meta={"demo": "support-agent"},
        )
