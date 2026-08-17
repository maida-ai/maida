"""Snapshots for the statistical gate's GitHub-facing Markdown report."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from maida.assertions import AssertionReport
from maida.runner_v2 import TrialRecord, TrialRunReport
from maida.schema_versions import REPORT_SCHEMA_VERSION
from maida.statistics import GateVerdict, StatisticalResult


def _result(
    check_name: str,
    *,
    kind: str,
    verdict: GateVerdict | None,
    mode: str = "gating",
    direction: str | None = None,
    evidence: dict | None = None,
) -> StatisticalResult:
    return StatisticalResult(
        check_name=check_name,
        kind=kind,
        verdict=verdict,
        decision_rule="report_only" if mode == "report_only" else "invariant",
        trials_used=1,
        trials_budgeted=1,
        direction=direction,
        mode=mode,
        trial_outcomes=(verdict is not GateVerdict.FAIL,),
        evidence=evidence or {},
    )


def _trial(*, baseline_diff: dict | None, passed: bool = True) -> TrialRecord:
    assertion_report = AssertionReport(
        run_id="a" * 32,
        baseline_run_id="b" * 32 if baseline_diff is not None else None,
        passed=passed,
    )
    return TrialRecord(
        trial=1,
        trace_id="a" * 32,
        run_name="orders-agent",
        process_exit_code=0,
        stdout="",
        stderr="",
        assertion_report=assertion_report,
        baseline_diff=baseline_diff,
    )


def test_trial_report_pass_markdown_snapshot() -> None:
    report = TrialRunReport(
        trials_requested=1,
        trials=[_trial(baseline_diff={})],
        aggregate_results=[
            _result(
                "agent_process",
                kind="invariant",
                verdict=GateVerdict.PASS,
                evidence={"violations": 0},
            ),
            _result(
                "step_count",
                kind="measured",
                verdict=GateVerdict.PASS,
                direction="upper",
                evidence={
                    "observed": 8.0,
                    "baseline": 8.0,
                    "allowed": {"lower": None, "upper": 10.0},
                    "delta": 0.0,
                },
            ),
        ],
    )

    assert report.to_markdown(baseline_path=".maida/baselines/orders.json") == dedent(
        """\
        ## ✅ Maida verdict: pass

        **2 blocking checks passed** · **1/1 trials passed**

        ### Behavior vs baseline

        No behavior changed from the accepted baseline across the sampled trials.

        ### Next steps

        - No gate action needed. Inspect the trace: `maida view aaaaaaaa`

        <details>
        <summary>Passing checks, report-only metrics, and trial evidence</summary>

        #### Passing checks

        - ✅ **Agent execution stayed healthy.** `agent_process` — All 1 trial completed successfully.
        - ✅ **Steps stayed within the allowed range.** `step_count` — Observed 8 (baseline 8; allowed at most 10).

        #### Trial evidence

        | Trial | Outcome | Trace | Process exit | Behavior changes |
        | ---: | --- | --- | ---: | --- |
        | 1 | PASS | `aaaaaaaa` | 0 | none |

        </details>

        ---
        *Gated by [Maida](https://maida.ai) — the local-first behavioral regression gate for AI agents.*"""
    )


def test_trial_report_fail_markdown_snapshot() -> None:
    baseline_diff = {
        "summary_diff": {
            "total_events": (12, 8),
            "total_tokens": (240, 100),
            "duration_ms": (900, 300),
            "loop_warnings": (2, 0),
        },
        "new_tools": ["delete|records"],
        "removed_tools": ["lookup"],
        "repeated_tools": {"retry": (1, 3)},
        "reordered_tools": True,
        "current_tool_sequence": ["retry", "delete|records"],
        "baseline_tool_sequence": ["lookup", "retry"],
        "guardrail_event_diff": (1, 0),
        "terminal_status_diff": ("error", "ok"),
    }
    report = TrialRunReport(
        trials_requested=1,
        trials=[_trial(baseline_diff=baseline_diff, passed=False)],
        aggregate_results=[
            _result(
                "agent_process",
                kind="invariant",
                verdict=GateVerdict.PASS,
                evidence={"violations": 0},
            ),
            _result(
                "no_loops",
                kind="invariant",
                verdict=GateVerdict.FAIL,
                evidence={"violations": 1},
            ),
            _result(
                "latency_ms",
                kind="measured",
                verdict=GateVerdict.FAIL,
                direction="upper",
                evidence={
                    "observed": 900.0,
                    "baseline": 300.0,
                    "allowed": {"lower": None, "upper": 600.0},
                    "delta": 600.0,
                },
            ),
            _result(
                "cost_tokens",
                kind="statistical",
                verdict=None,
                mode="report_only",
                direction="upper",
                evidence={
                    "successes": 1,
                    "observed_rate": 1.0,
                    "confidence": 0.95,
                    "confidence_bounds": {"lower": 0.27, "upper": 1.0},
                    "threshold": 0.9,
                },
            ),
        ],
        baseline_acceptance={
            "accepted_at": "2026-07-22T20:15:00.000Z",
            "accepted_by": "reviewer-login",
            "reason": "Expected retrieval | tool split",
            "source": {
                "pull_request": {
                    "number": 42,
                    "url": "https://github.com/maida-ai/example-agent/pull/42",
                },
                "commit_sha": "abcdef1234567890",
            },
            "verdict": {
                "outcome": "accepted",
                "summary": "Accepted run status ok: 8 events, 2 tool calls.",
            },
        },
    )

    markdown = report.to_markdown(baseline_path=".maida/baselines/orders.json")

    assert markdown == dedent(
        """\
        ## ❌ Maida verdict: fail

        **2 blocking checks failed** · **0/1 trials passed**

        ### Behavior vs baseline

        - Tool order changed from `lookup → retry` to `retry → delete\\|records`.
        - New tool used: `delete\\|records`.
        - Tool removed: `lookup`.
        - Tool `retry` repeated 3 times (baseline: 1).
        - Steps increased from 8 to 12 (+4).
        - Tokens increased from 100 to 240 (+140).
        - Latency increased from 300 ms to 900 ms (+600 ms).
        - Loops increased from 0 to 2 (+2).
        - Guardrails triggered 1 time (baseline: 0).
        - Terminal state changed from `ok` to `error`.

        ### Blocking checks

        - ❌ **Loops appeared in 1 of 1 trial.** `no_loops`
        - ❌ **Latency increased beyond the allowed range.** `latency_ms` — Observed 900 ms (baseline 300 ms; allowed at most 600 ms).

        ### Baseline provenance

        | Accepted by | Accepted at | Source |
        |---|---|---|
        | `reviewer-login` | `2026-07-22T20:15:00.000Z` | [PR #42](https://github.com/maida-ai/example-agent/pull/42) at `abcdef12` |

        **Acceptance verdict:** accepted — Accepted run status ok: 8 events, 2 tool calls.

        **Reason:** Expected retrieval \\| tool split

        ### Next steps

        - Review the behavioral changes and blocking checks above.
        - Inspect the full diff: `maida diff aaaaaaaa --baseline .maida/baselines/orders.json`
        - Open the trace locally: `maida view aaaaaaaa`
        - If this change is intentional, comment `/maida accept` on the PR or accept locally: `maida accept aaaaaaaa --baseline .maida/baselines/orders.json --reason "..."`
        - Otherwise fix the agent behavior and rerun: `maida run AGENT.py --baseline .maida/baselines/orders.json`

        <details>
        <summary>Passing checks, report-only metrics, and trial evidence</summary>

        #### Passing checks

        - ✅ **Agent execution stayed healthy.** `agent_process` — All 1 trial completed successfully.

        #### Report-only metrics

        - ℹ️ **Tokens were observed without blocking the gate.** `cost_tokens` — Observed pass rate 1.000; 95% confidence range 0.270–1.000.

        #### Trial evidence

        | Trial | Outcome | Trace | Process exit | Behavior changes |
        | ---: | --- | --- | ---: | --- |
        | 1 | FAIL | `aaaaaaaa` | 0 | 10 changes |

        </details>

        ---
        *Gated by [Maida](https://maida.ai) — the local-first behavioral regression gate for AI agents.*"""
    )
    assert markdown.count("<details>") == 1
    assert markdown.count("</details>") == 1


def test_trial_report_inconclusive_markdown_snapshot() -> None:
    report = TrialRunReport(
        trials_requested=3,
        trials=[_trial(baseline_diff=None)],
        aggregate_results=[
            _result(
                "agent_process",
                kind="invariant",
                verdict=GateVerdict.PASS,
                evidence={"violations": 0},
            ),
            _result(
                "task_pass_rate",
                kind="statistical",
                verdict=GateVerdict.INCONCLUSIVE,
                direction="lower",
                evidence={
                    "successes": 1,
                    "observed_rate": 1.0,
                    "confidence": 0.95,
                    "confidence_bounds": {"lower": 0.27, "upper": 1.0},
                    "threshold": 0.9,
                },
            ),
        ],
    )

    assert report.to_markdown() == dedent(
        """\
        ## ⚪ Maida verdict: inconclusive

        **1 blocking check inconclusive** · **1/3 trials completed**

        ### Behavior vs baseline

        No baseline comparison was configured for this run.

        ### Blocking checks

        - ⚪ **Successful behavior is still inconclusive.** `task_pass_rate` — 1/1 trials passed; 95% confidence range 0.270–1.000 (target at least 0.900).

        > Maida did not establish a blocking regression, but this sample is too small to approve confidently. Collect more trials and rerun before promotion.

        ### Next steps

        - Collect the remaining evidence and rerun: `maida run AGENT.py --trials 3`
        - Open the trace locally: `maida view aaaaaaaa`

        <details>
        <summary>Passing checks, report-only metrics, and trial evidence</summary>

        #### Passing checks

        - ✅ **Agent execution stayed healthy.** `agent_process` — All 1 trial completed successfully.

        #### Trial evidence

        | Trial | Outcome | Trace | Process exit | Behavior changes |
        | ---: | --- | --- | ---: | --- |
        | 1 | PASS | `aaaaaaaa` | 0 | not configured |

        </details>

        ---
        *Gated by [Maida](https://maida.ai) — the local-first behavioral regression gate for AI agents.*"""
    )


def test_trial_report_renders_legacy_acceptance_safely() -> None:
    report = TrialRunReport(
        trials_requested=1,
        baseline_acceptance={
            "accepted_at": "2026-07-09T12:00:00.000Z",
            "reason": "Legacy local | acceptance",
        },
    )

    markdown = report.to_markdown()

    assert "### Baseline provenance" in markdown
    assert "`unknown`" in markdown
    assert "local acceptance" in markdown
    assert "**Acceptance verdict:** accepted — not recorded" in markdown
    assert "**Reason:** Legacy local \\| acceptance" in markdown


def test_trial_report_baseline_acceptance_is_additive_json() -> None:
    acceptance = {
        "accepted_by": "reviewer-login",
        "reason": "Expected tool split",
    }

    legacy_payload = TrialRunReport(trials_requested=1).to_dict()
    current_payload = TrialRunReport(
        trials_requested=1,
        baseline_acceptance=acceptance,
    ).to_dict()

    assert "baseline_acceptance" not in legacy_payload
    assert current_payload["baseline_acceptance"] == acceptance
    assert (
        current_payload["report_version"]
        == legacy_payload["report_version"]
        == REPORT_SCHEMA_VERSION
    )
    assert current_payload["verdict"] == legacy_payload["verdict"] == "pass"


def test_statistical_report_schema_accepts_optional_baseline_acceptance() -> None:
    schema = json.loads(
        (
            Path(__file__).parents[1]
            / "schemas"
            / "statistical-gate-report.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert "baseline_acceptance" not in schema["required"]
    assert schema["properties"]["baseline_acceptance"]["type"] == [
        "object",
        "null",
    ]


@pytest.mark.parametrize("verdict", list(GateVerdict))
def test_trial_report_is_compact_for_five_or_fewer_checks(
    verdict: GateVerdict,
) -> None:
    results = [
        _result(
            f"check_{index}",
            kind="invariant",
            verdict=verdict,
            evidence={"violations": 0 if verdict is GateVerdict.PASS else 1},
        )
        for index in range(5)
    ]
    report = TrialRunReport(trials_requested=1, aggregate_results=results)

    markdown = report.to_markdown()

    assert markdown.count("<details>") == 1
    assert len(markdown.splitlines()) <= 45
