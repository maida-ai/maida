"""Assertion policy, result, and report types."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from maida.policy_types import MetricPolicy

kDefaultTolerance = 0.5  # 50% global default

KNOWN_CHECK_NAMES = frozenset(
    {
        "step_count",
        "tool_calls",
        "new_tools",
        "no_loops",
        "no_guardrails",
        "cost_tokens",
        "duration",
        "expect_status",
    }
)


class RegressionReasonCode(str, Enum):
    """Stable reason codes for assertion decisions and PR comment grouping."""

    NO_REGRESSION = "no_regression"
    STEP_COUNT_EXCEEDED = "step_count_exceeded"
    NEW_TOOL_PATH = "new_tool_path"
    TOOL_CALL_COUNT_EXCEEDED = "tool_call_count_exceeded"
    LOOP_DETECTED = "loop_detected"
    CYCLE_DETECTED = "cycle_detected"
    TERMINAL_STATE_MISSING = "terminal_state_missing"
    GUARDRAIL_EVENT_CHANGED = "guardrail_event_changed"
    LATENCY_ENVELOPE_EXCEEDED = "latency_envelope_exceeded"
    COST_ENVELOPE_EXCEEDED = "cost_envelope_exceeded"


@dataclass
class AssertionPolicy:
    """Policy configuration for assert checks.

    Note: all tolerances are fractional, not percentage.
    """

    # Repeated statistical gate settings
    trials: int = 3
    confidence_level: float = 0.95
    pass_rate_threshold: float = 0.90
    fail_fast: bool = True
    policy_version: tuple[int, int] = (2, 0)
    source_format: str = "cli"
    metrics: dict[str, MetricPolicy] = field(default_factory=dict)

    # Maximum allowed step count
    max_steps: int | None = None
    step_tolerance: float = kDefaultTolerance

    # Maximum allowed tool call count
    max_tool_calls: int | None = None
    tool_call_tolerance: float = kDefaultTolerance

    # Maximum allowed cost tokens
    max_cost_tokens: int | None = None
    cost_tolerance: float = kDefaultTolerance

    # Maximum allowed duration in milliseconds
    max_duration_ms: int | None = None
    duration_tolerance: float = kDefaultTolerance

    no_new_tools: bool = False  # Fail if run uses tools not in baseline
    no_loops: bool = False  # Fail if any LOOP_WARNING present
    no_guardrails: bool = False  # Fail if any guardrail was triggered
    expect_status: str | None = None  # Expected run status (ok or error)

    # Explicitly ignored checks (skipped even when thresholds/baseline are set)
    ignored_checks: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.ignored_checks is None:
            self.ignored_checks = []
        if self.metrics is None:
            self.metrics = {}
        self.validate()

    def validate(self) -> None:
        """Fail fast when policy values cannot produce a meaningful gate."""
        if isinstance(self.trials, bool) or not isinstance(self.trials, int) or self.trials < 1:
            raise ValueError("trials must be an integer of at least 1")
        self._validate_fraction("confidence_level", self.confidence_level, inclusive=False)
        self._validate_fraction("pass_rate_threshold", self.pass_rate_threshold, inclusive=True)
        for name in (
            "step_tolerance",
            "tool_call_tolerance",
            "cost_tolerance",
            "duration_tolerance",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a non-negative number")
        for name in (
            "max_steps",
            "max_tool_calls",
            "max_cost_tokens",
            "max_duration_ms",
        ):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or null")
        if self.expect_status not in {None, "ok", "error"}:
            raise ValueError("expect_status must be 'ok', 'error', or null")
        if not isinstance(self.fail_fast, bool):
            raise ValueError("fail_fast must be a boolean")
        if (
            not isinstance(self.policy_version, tuple)
            or len(self.policy_version) != 2
            or not all(isinstance(part, int) and part >= 0 for part in self.policy_version)
        ):
            raise ValueError("policy_version must be a (major, minor) tuple")
        if self.policy_version[0] != 2:
            raise ValueError("unsupported policy_version; use policy major 2")
        if self.source_format not in {"cli", "v2"}:
            raise ValueError("source_format must be cli or v2")
        if not isinstance(self.metrics, dict) or not all(
            isinstance(name, str) and isinstance(metric, MetricPolicy) for name, metric in self.metrics.items()
        ):
            raise ValueError("metrics must map names to MetricPolicy values")
        if not isinstance(self.ignored_checks, list) or not all(isinstance(name, str) for name in self.ignored_checks):
            raise ValueError("ignored_checks must be a list of check names")

    @staticmethod
    def _validate_fraction(name: str, value: object, *, inclusive: bool) -> None:
        valid_type = isinstance(value, (int, float)) and not isinstance(value, bool)
        if not valid_type or not math.isfinite(value):
            valid_range = False
        elif inclusive:
            valid_range = 0.0 <= value <= 1.0
        else:
            valid_range = 0.0 < value < 1.0
        if not valid_range:
            if inclusive:
                raise ValueError(f"{name} must be between 0 and 1")
            raise ValueError(f"{name} must be greater than 0 and less than 1")


@dataclass
class AssertionResult:
    """Result of a single assertion check."""

    check_name: str
    passed: bool
    message: str
    reason_code: RegressionReasonCode = RegressionReasonCode.NO_REGRESSION
    expected: str | None = None
    actual: str | None = None
    ignored: bool = False


@dataclass
class AssertionReport:
    """Full report from running assertions."""

    run_id: str
    baseline_run_id: str | None
    baseline_acceptance: dict | None = None
    results: list[AssertionResult] = field(default_factory=list)
    passed: bool = True

    def add(self, result: AssertionResult) -> None:
        self.results.append(result)
        if not result.passed:
            self.passed = False

    @property
    def reason_codes(self) -> list[RegressionReasonCode]:
        """Failure reason codes in result order, de-duplicated for machines."""
        return list(dict.fromkeys(result.reason_code for result in self.results if not result.passed))
