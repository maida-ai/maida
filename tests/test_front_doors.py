"""Keep the first report reachable without learning capture internals."""

import json
import os
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGES = ("README.md", "docs/getting-started.md", "docs/index.md")
CONTRACT = json.loads((ROOT / "contracts/current-main.json").read_text())
RELEASE = CONTRACT["engine_ref"].removeprefix("v")


def test_readme_star_request_follows_the_own_agent_report():
    text = (ROOT / "README.md").read_text()
    requests = re.findall(r"(?m)^.*(?:⭐|\bstar\b).*$", text)
    assert len(requests) == 1
    request = requests[0]
    assert "If Maida gave you a useful signal on your agent" in request
    assert "[star the repo](https://github.com/maida-ai/maida)" in request
    before = text[: text.index(request)].rstrip()
    report = before.split("\n\n")[-2:]
    assert "3 active checks passed" in report[0]
    assert "trace ID" in report[0] and "viewer command" in report[0]
    assert "maida view" in report[1] and "your own report" in report[1]
    assert text.index(request) < text.index("## Investigate a regression")


@pytest.mark.parametrize("relative", PAGES)
def test_first_workflow_uses_released_setup_check_and_printed_view(relative):
    text = (ROOT / relative).read_text()
    block = text.split("```bash\n", 1)[1].split("```", 1)[0]
    assert f'uv tool install "maida-ai=={RELEASE}"' in block
    assert text[: text.index("```bash")].count("\n") < 40
    assert "cd my-repo" in block
    assert block.index("maida init") < block.index("maida check") < block.index('exact "View:" command')
    assert "Claude Code task" in block and "exit" in block
    assert "3 active checks passed" in text
    assert text.index("maida view 83aa19e3") < text.index("## Protect the next agent change")


@pytest.mark.parametrize("relative", PAGES)
def test_bash_examples_do_not_use_redirection_placeholders(relative):
    text = (ROOT / relative).read_text()
    for block in re.findall(r"```bash\n(.*?)```", text, re.S):
        assert not re.search(r"<[A-Z][A-Z_]*>", block)


@pytest.mark.skipif(os.name == "nt", reason="Exercises documented Bash snippets")
def test_run_id_examples_reach_maida_as_arguments():
    text = (ROOT / "docs/getting-started.md").read_text()
    examples = [
        block
        for block in re.findall(r"```bash\n(.*?)```", text, re.S)
        if "--from-run" in block or "--baseline" in block
    ]
    assert len(examples) == 2
    for block in examples:
        result = subprocess.run(
            ["bash", "-eu", "-c", 'maida() { printf "%s\\n" "$@"; };\n' + block],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        arguments = result.stdout.splitlines()
        if "--from-run" in block:
            assert arguments == ["init", "--from-run", "paste-the-id-from-maida-check"]
        else:
            assert arguments[:3] == ["check", "assert", "paste-the-new-id-here"]
            assert arguments[3:] == ["--baseline", ".maida/baselines/agent.json", "--policy", ".maida/policy.yaml"]


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
