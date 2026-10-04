"""Keep the first report reachable without learning capture internals."""

import json
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGES = ("README.md", "docs/getting-started.md", "docs/index.md")
CONTRACT = json.loads((ROOT / "contracts/current-main.json").read_text())
RELEASE = CONTRACT["engine_ref"].removeprefix("v")


@pytest.mark.parametrize("relative", PAGES)
def test_first_workflow_uses_released_setup_check_and_printed_view(relative):
    text = (ROOT / relative).read_text()
    block = text.split("```bash\n", 1)[1].split("```", 1)[0]
    assert f'uv tool install "maida-ai=={RELEASE}"' in block
    assert text[: text.index("```bash")].count("\n") < 40
    assert "cd my-repo" in block
    assert block.index("maida init") < block.index("maida check") < block.index("maida view <TRACE_ID>")
    assert "Claude Code task" in block and "exit" in block
    assert "3 active checks passed" in text
    assert text.index("maida view <TRACE_ID>") < text.index("## Protect the next agent change")


@pytest.mark.parametrize("relative", PAGES)
def test_front_doors_do_not_restore_obsolete_onboarding(relative):
    text = (ROOT / relative).read_text()
    assert not re.search(r"unreleased|wheel.{0,50}main|main.{0,50}wheel", text, re.I)
    for obsolete in (
        "onboarding/install_capture",
        "MAIDA_DATA_DIR",
        "--expect-status",
        "--no-loops",
        "--no-guardrails",
    ):
        assert obsolete not in text
    for reference in re.findall(r"maida-ai/maida-assert@[^\s`]+", text):
        assert reference == CONTRACT["action_ref"] or re.fullmatch(r"maida-ai/maida-assert@[0-9a-f]{40}", reference)
    assert set(re.findall(r"maida-ai(?:\[[^]]+\])?==([\d.]+)", text)) == {RELEASE}
    assert set(re.findall(r"Maida (?:v)?(\d+\.\d+\.\d+)", text)) <= {RELEASE}
    assert "$15" in text and "VIP" in text


def test_job_index_keeps_technical_reference_discoverable():
    text = (ROOT / "docs/index.md").read_text()
    for job in (
        "Try Maida",
        "Check an agent change",
        "Investigate a regression",
        "Protect the next agent change",
        "Add the PR gate",
        "Integrate another agent/framework",
        "Reference",
    ):
        assert f"## {job}" in text
    for reference in (
        "reference/policy.md",
        "reference/trace-format.md",
        "reference/trace-emitter.md",
        "reference/config.md",
        "architecture.md",
        "sdk.md",
    ):
        assert reference in text
