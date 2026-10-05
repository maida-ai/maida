"""Storage and trace-format errors."""


class RunValidationError(RuntimeError):
    """A stored run exists but does not match the supported trace contract."""

    def __init__(self, trace_id: str, problem: str) -> None:
        self.trace_id = trace_id
        self.problem = problem
        short = trace_id[:8] if isinstance(trace_id, str) and trace_id else "unknown"
        super().__init__(
            f"Run validation failed for {short}: {problem}. "
            "Next step: rerun the traced agent to create a fresh run, "
            "or run `maida demo` to create a known-good local trace."
        )


class UnsupportedTraceFormatError(RuntimeError):
    """A stored run uses a trace format Maida cannot project safely."""

    def __init__(self, run_id: str, problem: str) -> None:
        self.run_id = run_id
        self.problem = problem
        short = run_id[:8] if isinstance(run_id, str) and run_id else "unknown"
        super().__init__(
            f"unsupported trace format for run {short}: {problem}. "
            "The earliest fully supported trace format is 0.2. "
            "Next step: rerun the traced agent to create a fresh trace, "
            "or run `maida demo` to create a known-good local trace."
        )
