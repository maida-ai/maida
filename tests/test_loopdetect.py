"""
Loop detection tests: synthetic events with repeated tail pattern, detect_loop payload shape and stability.
No I/O; uses in-memory events. pattern_key stability and calling detect_loop again yields same payload.
"""

import json

import pytest

from maida.loopdetect import compute_signature, detect_loop, pattern_key


def _make_event(event_id: str, event_type: str, payload: dict) -> dict:
    """Minimal event dict for loop detection (signature comes from event_type + payload)."""
    return {
        "event_id": event_id,
        "event_type": event_type,
        "payload": payload,
    }


def test_detect_loop_repeated_tail_payload_shape_and_evidence_count():
    """Synthetic events with repeated tail; payload has pattern, repetitions, window_size, evidence_event_ids."""
    # Pattern [TOOL_CALL:foo, LLM_CALL:gpt] repeated 3 times -> 6 events in tail
    ids = [f"id-{i}" for i in range(6)]
    events = [
        _make_event(ids[0], "TOOL_CALL", {"tool_name": "foo"}),
        _make_event(ids[1], "LLM_CALL", {"model": "gpt"}),
        _make_event(ids[2], "TOOL_CALL", {"tool_name": "foo"}),
        _make_event(ids[3], "LLM_CALL", {"model": "gpt"}),
        _make_event(ids[4], "TOOL_CALL", {"tool_name": "foo"}),
        _make_event(ids[5], "LLM_CALL", {"model": "gpt"}),
    ]
    window = 10
    repetitions = 3
    payload = detect_loop(events, window=window, repetitions=repetitions)
    assert payload is not None
    assert "pattern" in payload
    assert "repetitions" in payload
    assert "window_size" in payload
    assert "evidence_event_ids" in payload

    pattern_len = 2  # TOOL_CALL:foo, LLM_CALL:gpt
    assert len(payload["evidence_event_ids"]) == pattern_len * repetitions

    assert payload["pattern"] == "TOOL_CALL:foo -> LLM_CALL:gpt"
    assert pattern_key(payload) == "TOOL_CALL:foo -> LLM_CALL:gpt|3"


def test_detect_loop_called_again_yields_same_payload():
    """Calling detect_loop again yields same payload (no dedupe inside detect_loop)."""
    events = []
    for i in range(6):
        events.append(
            _make_event(
                f"e-{i}",
                "TOOL_CALL" if i % 2 == 0 else "LLM_CALL",
                {"tool_name": "x"} if i % 2 == 0 else {"model": "y"},
            )
        )
    payload1 = detect_loop(events, window=10, repetitions=3)
    payload2 = detect_loop(events, window=10, repetitions=3)
    assert payload1 is not None
    assert payload2 is not None
    assert payload1["pattern"] == payload2["pattern"]
    assert payload1["evidence_event_ids"] == payload2["evidence_event_ids"]
    assert pattern_key(payload1) == pattern_key(payload2)


def test_detect_loop_smallest_m_chosen():
    """When multiple pattern lengths could match, the smallest m is chosen."""
    # 12 events: (TOOL_CALL:foo, LLM_CALL:gpt) x 6. Both m=2 (block x3) and m=4 (block x3) could match.
    # Algorithm iterates m from 2; it should return m=2, so pattern "TOOL_CALL:foo -> LLM_CALL:gpt".
    events = []
    for i in range(12):
        events.append(
            _make_event(
                f"id-{i}",
                "TOOL_CALL" if i % 2 == 0 else "LLM_CALL",
                {"tool_name": "foo"} if i % 2 == 0 else {"model": "gpt"},
            )
        )
    payload = detect_loop(events, window=12, repetitions=3)
    assert payload is not None
    assert payload["pattern"] == "TOOL_CALL:foo -> LLM_CALL:gpt"
    assert len(payload["evidence_event_ids"]) == 2 * 3  # m=2, not 4*3


def test_detect_loop_no_loop_returns_none():
    """When the tail does not contain a consecutively repeating pattern, returns None."""
    # All different signatures: no repeated block.
    events = [
        _make_event("a", "TOOL_CALL", {"tool_name": "one"}),
        _make_event("b", "TOOL_CALL", {"tool_name": "two"}),
        _make_event("c", "TOOL_CALL", {"tool_name": "three"}),
        _make_event("d", "LLM_CALL", {"model": "m1"}),
        _make_event("e", "LLM_CALL", {"model": "m2"}),
    ]
    assert detect_loop(events, window=10, repetitions=3) is None


def test_loop_warning_triggers_on_single_event_repetition():
    """A single event signature repeating >= repetitions times triggers LOOP_WARNING (m=1)."""
    repetitions = 3
    events = [
        _make_event("e-0", "TOOL_CALL", {"tool_name": "search_db"}),
        _make_event("e-1", "TOOL_CALL", {"tool_name": "search_db"}),
        _make_event("e-2", "TOOL_CALL", {"tool_name": "search_db"}),
    ]
    payload = detect_loop(events, window=12, repetitions=repetitions)
    assert payload is not None, "m=1 repetition must be detected"
    assert payload["pattern"] == "TOOL_CALL:search_db"
    assert payload["repetitions"] == repetitions
    assert len(payload["evidence_event_ids"]) == 1 * repetitions
    assert payload["evidence_event_ids"] == ["e-0", "e-1", "e-2"]
    # pattern_key should encode the single-event pattern
    assert pattern_key(payload) == "TOOL_CALL:search_db|3"


def test_loop_warning_does_not_trigger_when_below_repetitions():
    """Only 2 repeats when repetitions=3 -> no LOOP_WARNING."""
    events = [
        _make_event("e-0", "TOOL_CALL", {"tool_name": "search_db"}),
        _make_event("e-1", "TOOL_CALL", {"tool_name": "search_db"}),
    ]
    assert detect_loop(events, window=12, repetitions=3) is None


@pytest.mark.parametrize("events,window,repetitions", [([], 12, 3), ([{}] * 3, 1, 3), ([{}] * 3, 12, 1)])
def test_detect_loop_insufficient_evidence_or_invalid_limits_do_not_warn(events, window, repetitions):
    assert detect_loop(events, window=window, repetitions=repetitions) is None


def test_detect_loop_distinct_calls_with_same_argument_shape():
    """Different queries to the same tool are distinct actions."""
    events = [
        _make_event(
            f"e-{i}",
            "TOOL_CALL",
            {
                "tool_name": "search_db",
                "args": {
                    "query": query,
                    "filters": {"limit": 5 + i, "include_archived": False},
                },
            },
        )
        for i, query in enumerate(("alpha", "beta", "gamma"))
    ]

    payload = detect_loop(events, window=12, repetitions=3)

    assert payload is None


def test_detect_loop_alternating_tool_cycle():
    """Alternating A-B-A-B tool calls are reported as a compact cycle signature."""
    events = [
        _make_event("e-0", "TOOL_CALL", {"tool_name": "search"}),
        _make_event("e-1", "TOOL_CALL", {"tool_name": "summarize"}),
        _make_event("e-2", "TOOL_CALL", {"tool_name": "search"}),
        _make_event("e-3", "TOOL_CALL", {"tool_name": "summarize"}),
    ]

    payload = detect_loop(events, window=12, repetitions=2)

    assert payload is not None
    assert payload["pattern_type"] == "cycle"
    assert payload["pattern_length"] == 2
    assert payload["pattern"] == "TOOL_CALL:search -> TOOL_CALL:summarize"


def test_detect_loop_alternating_tool_cycle_at_default_repetitions():
    """Alternating A-B cycles are detected at the default repetition threshold."""
    events = [
        _make_event(f"e-{i}", "TOOL_CALL", {"tool_name": name})
        for i, name in enumerate(("search", "summarize", "search", "summarize", "search", "summarize"))
    ]

    payload = detect_loop(events, window=12, repetitions=3)

    assert payload is not None
    assert payload["pattern_type"] == "cycle"
    assert payload["pattern_length"] == 2
    assert payload["repetitions"] == 3
    assert payload["pattern"] == "TOOL_CALL:search -> TOOL_CALL:summarize"
    assert payload["evidence_event_ids"] == [f"e-{i}" for i in range(6)]


def test_detect_loop_noisy_argument_values_are_distinct_observed_actions():
    """No argument keys are silently excluded from action identity."""
    events = [
        _make_event(
            f"e-{i}",
            "TOOL_CALL",
            {
                "tool_name": "fetch_status",
                "args": {
                    "request_id": f"req-{i}",
                    "timestamp": f"2026-06-27T00:00:0{i}Z",
                    "payload": {"retry": i, "source": "agent"},
                },
            },
        )
        for i in range(3)
    ]

    payload = detect_loop(events, window=12, repetitions=3)

    assert payload is None


def test_detect_loop_deep_argument_values_are_distinct():
    """Differences beyond the old shape depth limit still identify distinct actions."""
    events = [
        _make_event(
            f"e-{i}",
            "TOOL_CALL",
            {
                "tool_name": "inspect_tree",
                "args": {
                    "root": {
                        "branch": {
                            "leaf": {
                                "hidden": {
                                    "value": f"payload-{i}",
                                }
                            }
                        }
                    }
                },
            },
        )
        for i in range(3)
    ]

    payload = detect_loop(events, window=12, repetitions=3)

    assert payload is None


def test_detect_loop_different_argument_shapes_are_not_same_loop():
    """Same tool can repeat harmlessly when calls use different structural args."""
    events = [
        _make_event("e-0", "TOOL_CALL", {"tool_name": "search", "args": {"query": "a"}}),
        _make_event(
            "e-1",
            "TOOL_CALL",
            {"tool_name": "search", "args": {"query": "b", "page": 2}},
        ),
        _make_event(
            "e-2",
            "TOOL_CALL",
            {"tool_name": "search", "args": {"query": "c", "sort": "date"}},
        ),
    ]

    assert detect_loop(events, window=12, repetitions=3) is None


def test_detect_loop_legitimate_repetition_can_pass_under_policy():
    """Policy can allow limited repetition by requiring more repeats before warning."""
    events = [_make_event(f"e-{i}", "TOOL_CALL", {"tool_name": "poll_status"}) for i in range(3)]

    assert detect_loop(events, window=12, repetitions=4) is None


def test_detect_loop_uses_tail_window_for_long_traces():
    """Long traces are evaluated against the configured tail window only."""
    prefix = [_make_event(f"prefix-{i}", "TOOL_CALL", {"tool_name": f"step_{i}"}) for i in range(30)]
    loop = [
        _make_event(
            f"loop-{i}",
            "TOOL_CALL",
            {"tool_name": "retry_lookup", "args": {"query": "same-query"}},
        )
        for i in range(3)
    ]

    payload = detect_loop(prefix + loop, window=6, repetitions=3)

    assert payload is not None
    assert payload["window_size"] == 6
    assert payload["evidence_event_ids"] == ["loop-0", "loop-1", "loop-2"]


@pytest.mark.parametrize(
    "tool,key,values",
    [
        ("Bash", "command", ["pytest", "git status", "cat pyproject.toml"]),
        ("Read", "file_path", ["a.py", "b.py", "c.py"]),
    ],
)
def test_distinct_generic_tool_calls_do_not_warn(tool, key, values):
    events = [
        _make_event(f"e-{i}", "TOOL_CALL", {"tool_name": tool, "args": {key: value}}) for i, value in enumerate(values)
    ]
    assert detect_loop(events, window=12, repetitions=3) is None


@pytest.mark.parametrize("commands,length", [(["pytest"] * 3, 1), (["pytest", "git status"] * 3, 2)])
def test_same_tool_argument_identity_detects_repeated_calls_and_cycles(commands, length):
    events = [
        _make_event(f"e-{i}", "TOOL_CALL", {"tool_name": "Bash", "args": {"command": command}})
        for i, command in enumerate(commands)
    ]
    warning = detect_loop(events, window=12, repetitions=3)
    assert warning is not None
    assert warning["pattern_length"] == length
    assert warning["pattern_type"] == ("cycle" if length == 2 else "repeated_call")
    assert warning["evidence_event_ids"] == [f"e-{i}" for i in range(len(commands))]
    assert all(command not in json.dumps(warning) for command in commands)
    assert len(set(warning["pattern"].split(" -> "))) == length


def test_argument_identity_is_canonical_and_bounded():
    args = {"z": [True, None, 1, 1.5, "é😀"], "a": {"command": "private-command" * 1000}}
    event = _make_event("e", "TOOL_CALL", {"tool_name": "Bash", "args": args})
    signature = compute_signature(event)
    reordered = {**event, "payload": {"tool_name": "Bash", "args": {"a": args["a"], "z": args["z"]}}}
    assert signature == compute_signature(reordered)
    assert signature.startswith("TOOL_CALL:Bash args:sha256:")
    assert len(signature.removeprefix("TOOL_CALL:Bash args:sha256:")) == 64
    assert "private-command" not in signature


@pytest.mark.parametrize(
    "left,right",
    [
        ([1, 2, 3, 4], [1, 2, 3, 5]),
        ([1, 2], [2, 1]),
        (True, 1),
        ("1", 1),
        ({}, []),
    ],
)
def test_argument_identity_preserves_values_types_and_sequence_order(left, right):
    def signature(args):
        return compute_signature(_make_event("e", "TOOL_CALL", {"tool_name": "inspect", "args": args}))

    assert signature(left) != signature(right)


@pytest.mark.parametrize("left,right", [(1, 1.0), (0, -0.0), ([1], (1,))])
def test_argument_identity_normalizes_equivalent_json_values(left, right):
    def signature(args):
        return compute_signature(_make_event("e", "TOOL_CALL", {"tool_name": "inspect", "args": args}))

    assert signature(left) == signature(right)


@pytest.mark.parametrize("number", [2**53 + 1, 10**400])
def test_argument_identity_preserves_large_python_integers(number):
    def signature(args):
        return compute_signature(_make_event("e", "TOOL_CALL", {"tool_name": "inspect", "args": args}))

    assert signature(number) != signature(number + 1)


def test_argument_identity_rejects_unnormalized_objects_without_rendering_values():
    with pytest.raises(TypeError, match="Loop arguments must be normalized JSON values"):
        compute_signature(_make_event("e", "TOOL_CALL", {"tool_name": "inspect", "args": {"value": object()}}))
