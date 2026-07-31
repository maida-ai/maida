"""Immutable baseline sample coverage for issue #187."""

from maida.baseline_sample import create_baseline_from_report


def test_baseline_from_report_keeps_vectors_and_deduplicates_signatures() -> None:
    signature = {
        "tool_path": ["read"],
        "tool_call_sequence": ["read"],
        "tool_call_counts": {"read": 1},
        "llm_models_used": [],
        "event_type_sequence": ["RUN_START", "TOOL_CALL", "RUN_END"],
        "final_status": "ok",
    }
    report = {
        "report_version": "2.0.0",
        "metadata": {
            "trials_used": 3,
            "trials_budgeted": 3,
            "environment_fingerprint": {"workspace": "abc"},
        },
        "trials": [
            {
                "trace_id": f"{index:032x}",
                "run_name": "sample",
                "metric_values": {
                    "step_count": value,
                    "tool_call_count": 1,
                    "cost_tokens": 0,
                    "latency_ms": 5,
                },
                "invariant_outcomes": {"stop_condition_reached": True},
                "structural_signature": signature,
            }
            for index, value in enumerate([12, 11, 12], start=1)
        ],
    }
    baseline = create_baseline_from_report(report)
    sample = baseline["trial_sample"]
    assert baseline["schema_version"] == "0.3.0"
    assert sample["metrics"]["step_count"] == [12.0, 11.0, 12.0]
    assert sample["environment_fingerprint"] == {"workspace": "abc"}
    assert len(sample["signatures"]) == 1
    assert list(sample["signature_counts"].values()) == [3]


def test_baseline_rejects_fail_fast_partial_report() -> None:
    report = {
        "report_version": "2.0.0",
        "metadata": {
            "trials_used": 1,
            "trials_budgeted": 3,
            "environment_fingerprint": {},
        },
        "trials": [{}],
    }
    try:
        create_baseline_from_report(report)
    except ValueError as error:
        assert "--no-fail-fast" in str(error)
    else:
        raise AssertionError("partial report unexpectedly accepted")
