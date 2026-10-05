"""Plan contract error types."""

from __future__ import annotations


class PlanContractError(ValueError):
    """A plan artifact or evidence value violated the core contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> PlanContractError:
    return PlanContractError(code, message)
