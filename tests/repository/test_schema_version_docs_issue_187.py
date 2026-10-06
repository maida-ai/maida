"""Keep the five independent schema streams documented together."""

from tests.support.paths import REPO_ROOT
from maida.schema_versions import (
    BASELINE_SCHEMA_VERSION,
    PLAN_SCHEMA_VERSION,
    POLICY_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    TRACE_SCHEMA_VERSION,
)


def test_every_emitted_schema_version_appears_in_compatibility_table() -> None:
    reference = (REPO_ROOT / "docs" / "reference" / "policy.md").read_text(encoding="utf-8")
    for version in [
        POLICY_SCHEMA_VERSION,
        TRACE_SCHEMA_VERSION,
        BASELINE_SCHEMA_VERSION,
        REPORT_SCHEMA_VERSION,
        PLAN_SCHEMA_VERSION,
    ]:
        assert f"`{version}`" in reference
