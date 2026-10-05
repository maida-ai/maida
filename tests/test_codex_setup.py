"""Safe repository setup for the shared Codex and local Work runtime."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from maida.cli import app
from maida.config import load_config

runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "repository with spaces"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("MAIDA_DATA_DIR", raising=False)
    monkeypatch.setattr("maida.first_run.is_interactive", lambda: True)
    monkeypatch.setattr("maida.first_run.shutil.which", lambda name: None)
    monkeypatch.setattr("maida.first_run.validate_hook_command", lambda command: None)
    return root


def initialize(agent="codex", answer="y\n"):
    result = runner.invoke(app, ["init", "--agent", agent], input=answer)
    assert result.exit_code == 0, result.output
    return result


def pointer(project):
    return json.loads((project / ".maida/local.json").read_text())


def test_native_setup_preserves_hooks_modes_and_repeat(project):
    path = project / ".codex/hooks.json"
    path.parent.mkdir()
    original = {"description": "Team hooks", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}}
    path.write_text(json.dumps(original))
    path.chmod(0o600)
    result = initialize()
    assert result.output.index("Would") < result.output.index("[y/N]")
    assert "awaiting first capture" in result.output
    assert "/hooks" in result.output and "review" in result.output.lower()
    assert "session open" in result.output
    settings = json.loads(path.read_text())
    assert settings["description"] == original["description"]
    assert settings["hooks"]["Stop"][0] == original["hooks"]["Stop"][0]
    assert settings["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"] <= 3
    assert pointer(project)["providers"] == {"codex": {"enabled": True}}
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
    paths = [path, project / ".maida/local.json", project / ".git/info/exclude"]
    before = [item.read_bytes() for item in paths]
    repeat = initialize(answer="")
    assert "[y/N]" not in repeat.output
    assert before == [item.read_bytes() for item in paths]


def test_work_needs_no_cli_and_merges_existing_marketplace(project):
    path = project / ".agents/plugins/marketplace.json"
    path.parent.mkdir(parents=True)
    existing = {
        "name": "team",
        "interface": {"displayName": "Team tools"},
        "plugins": [{"name": "other", "source": "./plugins/other"}],
    }
    path.write_text(json.dumps(existing))
    result = initialize("chatgpt-work")
    assert not (project / ".codex").exists()
    marketplace = json.loads(path.read_text())
    assert marketplace["name"] == "team"
    assert marketplace["interface"] == existing["interface"]
    assert marketplace["plugins"][0] == existing["plugins"][0]
    assert marketplace["plugins"][1]["source"]["path"] == "./.maida/plugins/maida-capture"
    manifest = json.loads((project / ".maida/plugins/maida-capture/.codex-plugin/plugin.json").read_text())
    assert manifest["hooks"] == "./hooks/hooks.json"
    assert "Restart" in result.output and "install" in result.output.lower()
    assert "trust" in result.output.lower() and "local-only" in result.output
    assert "awaiting first capture" in result.output and "Work Cloud" in result.output
    assert pointer(project)["providers"] == {"chatgpt-work": {"enabled": True}}


def test_legacy_upgrade_preserves_identity_and_capture_store(project):
    initialize("claude-code")
    old = pointer(project)
    store = load_config(capture=True).data_dir
    initialize()
    upgraded = pointer(project)
    assert upgraded["version"] == 2
    assert upgraded["project_id"] == old["project_id"]
    assert upgraded["providers"] == {"claude-code": {"enabled": True}, "codex": {"enabled": True}}
    assert load_config(capture=True).data_dir == store
    initialize("chatgpt-work")
    assert len(pointer(project)["providers"]) == 3
    assert runner.invoke(app, ["init"]).exit_code == 2


@pytest.mark.parametrize("agent", ["codex", "chatgpt-work"])
def test_decline_and_noninteractive_make_no_files(project, monkeypatch, agent):
    exclude = (project / ".git/info/exclude").read_bytes()
    initialize(agent, "n\n")
    assert not (project / ".maida").exists()
    assert not (project / ".codex").exists()
    assert not (project / ".agents").exists()
    assert (project / ".git/info/exclude").read_bytes() == exclude
    monkeypatch.setattr("maida.first_run.is_interactive", lambda: False)
    result = runner.invoke(app, ["init", "--agent", agent])
    assert result.exit_code == 2 and "interactive terminal" in result.output
    assert not (project / ".maida").exists()


@pytest.mark.parametrize("content", ["invalid", "[]", '{"hooks": []}', '{"hooks":{"Stop":[{}]}}'])
def test_malformed_native_configuration_is_preserved(project, content):
    path = project / ".codex/hooks.json"
    path.parent.mkdir()
    path.write_text(content)
    result = runner.invoke(app, ["init", "--agent", "codex"], input="y\n")
    assert result.exit_code == 2 and "rerun maida init" in result.output
    assert path.read_text() == content
    assert not (project / ".maida").exists()


@pytest.mark.parametrize(
    "content",
    [
        '{"plugins":{}}',
        '{"name":"team", "plugins":[42]}',
        '{"name":"team", "plugins":[{"name":"maida-capture", "source":"./other"}]}',
    ],
)
def test_invalid_or_conflicting_marketplace_is_preserved(project, content):
    path = project / ".agents/plugins/marketplace.json"
    path.parent.mkdir(parents=True)
    path.write_text(content)
    result = runner.invoke(app, ["init", "--agent", "chatgpt-work"], input="y\n")
    assert result.exit_code == 2
    assert path.read_text() == content
    assert not (project / ".maida").exists()


def test_concurrent_change_and_rollback(project, monkeypatch):
    import maida.first_run as first_run

    path = project / ".codex/hooks.json"
    path.parent.mkdir()
    path.write_text("{}")

    def concurrent(*args, **kwargs):
        path.write_text('{"description":"concurrent"}')
        return True

    monkeypatch.setattr(first_run.typer, "confirm", concurrent)
    result = runner.invoke(app, ["init", "--agent", "codex"])
    assert result.exit_code == 2 and "changed after preview" in result.output
    assert not (project / ".maida/local.json").exists()
    monkeypatch.setattr(first_run.typer, "confirm", lambda *args, **kwargs: True)
    replace = first_run.atomic_replace

    def failing(path, content, *, expected, **kwargs):
        if path.name == "exclude":
            raise PermissionError("fixture")
        replace(path, content, expected=expected, **kwargs)

    monkeypatch.setattr(first_run, "atomic_replace", failing)
    result = runner.invoke(app, ["init", "--agent", "chatgpt-work"])
    assert result.exit_code == 2 and "restored" in result.output
    assert not (project / ".maida/local.json").exists()
    assert not (project / ".maida/plugins/maida-capture/hooks/hooks.json").exists()
    assert not (project / ".agents/plugins/marketplace.json").exists()


@pytest.mark.parametrize("agent", ["codex", "chatgpt-work"])
def test_detach_shared_runtime_keeps_claude_and_saved_evidence(project, agent):
    initialize("claude-code")
    claude = (project / ".claude/settings.local.json").read_bytes()
    initialize()
    initialize("chatgpt-work")
    evidence = load_config(capture=True).data_dir / "retained.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("saved evidence")
    result = runner.invoke(app, ["detach", "--agent", agent], input="y\n")
    assert result.exit_code == 0, result.output
    assert "both Codex and local-only ChatGPT Work" in result.output
    assert (project / ".claude/settings.local.json").read_bytes() == claude
    assert evidence.read_text() == "saved evidence"
    states = pointer(project)["providers"]
    assert states["claude-code"]["enabled"]
    assert not states["codex"]["enabled"] and not states["chatgpt-work"]["enabled"]
    assert not json.loads((project / ".codex/hooks.json").read_text()).get("hooks")
    assert json.loads((project / ".agents/plugins/marketplace.json").read_text())["plugins"] == []
    assert not json.loads((project / ".maida/plugins/maida-capture/hooks/hooks.json").read_text()).get("hooks")
    initialize("claude-code", answer="")
    assert not pointer(project)["providers"]["codex"]["enabled"]


def test_claude_detach_is_independent_on_v2(project):
    initialize("claude-code")
    initialize()
    codex = (project / ".codex/hooks.json").read_bytes()
    result = runner.invoke(app, ["detach", "--agent", "claude-code"], input="y\n")
    assert result.exit_code == 0, result.output
    assert (project / ".codex/hooks.json").read_bytes() == codex
    assert pointer(project)["providers"] == {"claude-code": {"enabled": False}, "codex": {"enabled": True}}


def test_tracked_native_hooks_warn_and_machine_artifacts_excluded(project):
    path = project / ".codex/hooks.json"
    path.parent.mkdir()
    path.write_text("{}")
    subprocess.run(["git", "add", ".codex/hooks.json"], check=True)
    assert "TRACKED" in initialize().output
    initialize("chatgpt-work")
    for path in (
        ".maida/local.json",
        ".maida/plugins/maida-capture/hooks/hooks.json",
        ".agents/plugins/marketplace.json",
    ):
        assert subprocess.run(["git", "check-ignore", path], capture_output=True).returncode == 0


def test_detect_native_provider_and_attached_signal(project):
    (project / ".codex").mkdir()
    result = runner.invoke(app, ["init"], input="y\n")
    assert result.exit_code == 0, result.output
    (project / ".claude").mkdir()
    assert runner.invoke(app, ["init"]).exit_code == 0


@pytest.mark.parametrize(
    "content",
    [
        "[features]\nhooks = false\n",
        "[features]\ncodex_hooks = false\n",
        "allow_managed_hooks_only = true\n",
        '[features]\nhooks = "false"\n',
        "invalid toml",
    ],
)
def test_disabled_or_malformed_native_configuration_has_no_writes(project, content):
    config = project / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text(content)
    result = runner.invoke(app, ["init", "--agent", "codex"], input="y\n")
    assert result.exit_code == 2 and "rerun maida init" in result.output
    assert config.read_text() == content
    assert not (project / ".maida").exists()
    assert not (project / ".codex/hooks.json").exists()


def test_hook_probe_failure_precedes_preview_and_approval(project, monkeypatch):
    def failed(command):
        raise ValueError("Repair the installed hook and rerun maida init.")

    monkeypatch.setattr("maida.first_run.validate_hook_command", failed)
    result = runner.invoke(app, ["init", "--agent", "codex"], input="y\n")
    assert result.exit_code == 2 and "Repair" in result.output
    assert "[y/N]" not in result.output
    assert not (project / ".maida").exists()


def test_detach_preserves_unrelated_native_and_marketplace_entries(project):
    from maida.codex_setup import merged_marketplace

    path = project / ".codex/hooks.json"
    path.parent.mkdir()
    other = {"type": "command", "command": "echo maida capture codex-hook"}
    path.write_text(json.dumps({"description": "Other hook", "hooks": {"Stop": [{"hooks": [other]}]}}))
    initialize()
    catalog = project / ".agents/plugins/marketplace.json"
    catalog.parent.mkdir(parents=True)
    unrelated = {"name": "other", "source": "./plugins/other"}
    catalog.write_text(json.dumps(merged_marketplace({"name": "team", "plugins": [unrelated]})))
    initialize("chatgpt-work")
    result = runner.invoke(app, ["detach", "--agent", "codex"], input="y\n")
    assert result.exit_code == 0, result.output
    assert json.loads(path.read_text()) == {"description": "Other hook", "hooks": {"Stop": [{"hooks": [other]}]}}
    assert json.loads(catalog.read_text())["plugins"] == [unrelated]


def test_detach_decline_and_repair_moved_interpreter_need_approval(project, monkeypatch):
    initialize()
    path = project / ".codex/hooks.json"
    before = path.read_bytes()
    old_id = pointer(project)["project_id"]
    result = runner.invoke(app, ["detach", "--agent", "codex"], input="n\n")
    assert result.exit_code == 0 and path.read_bytes() == before
    monkeypatch.setattr(
        "maida.first_run.bound_hook_command",
        lambda **kwargs: "/new/environment/python -E -P -m maida.cli capture codex-hook",
    )
    initialize(answer="n\n")
    assert path.read_bytes() == before
    result = initialize()
    assert "renewed" in result.output
    assert pointer(project)["project_id"] == old_id
    assert all(
        group["hooks"][0]["command"].startswith("/new/environment/python")
        for groups in json.loads(path.read_text())["hooks"].values()
        for group in groups
    )


def test_native_feature_config_concurrent_change_aborts_entire_preview(project, monkeypatch):
    config = project / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text("[features]\nhooks = true\n")

    def concurrent(*args, **kwargs):
        config.write_text("[features]\nhooks = false\n")
        return True

    monkeypatch.setattr("maida.first_run.typer.confirm", concurrent)
    result = runner.invoke(app, ["init", "--agent", "codex"])
    assert result.exit_code == 2 and "changed after preview" in result.output
    assert not (project / ".maida/local.json").exists()
    assert not (project / ".codex/hooks.json").exists()


def test_runtime_detach_preserves_damaged_pointer_and_removes_owned_hooks(project):
    initialize()
    path = project / ".maida/local.json"
    path.write_text("damaged")
    result = runner.invoke(app, ["detach", "--agent", "codex"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "unreadable and preserved" in result.output
    assert path.read_text() == "damaged"
    assert not json.loads((project / ".codex/hooks.json").read_text()).get("hooks")


def test_setup_refuses_symlinked_native_path(project, symlink_supported):
    target = project / "existing.json"
    target.write_text("{}")
    path = project / ".codex/hooks.json"
    path.parent.mkdir()
    path.symlink_to(target)
    result = runner.invoke(app, ["init", "--agent", "codex"], input="y\n")
    assert result.exit_code == 2 and "symlink" in result.output
    assert target.read_text() == "{}"
    assert not (project / ".maida").exists()


def test_detaching_unattached_claude_does_not_change_codex_identity(project):
    initialize()
    path = project / ".maida/local.json"
    before = path.read_bytes()
    result = runner.invoke(app, ["detach", "--agent", "claude-code"])
    assert result.exit_code == 0, result.output
    assert path.read_bytes() == before
    assert "[y/N]" not in result.output


@pytest.mark.parametrize(
    "command",
    [
        '"/environment/$(echo changed)/python" -E -P -m maida.cli capture codex-hook',
        '"/environment/$HOME/python" -E -P -m maida.cli capture codex-hook',
        "echo maida capture codex-hook",
    ],
)
def test_detach_ownership_requires_canonical_observer_command(command):
    from maida.codex_setup import is_codex_observer, removed_hooks

    assert not is_codex_observer(command)
    settings = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}}
    retained, count = removed_hooks(settings)
    assert retained == settings and count == 0


@pytest.mark.parametrize("maida_field", ["command", "commandWindows"])
def test_mixed_platform_hook_preserves_unrelated_command_on_setup_and_detach(project, maida_field):
    from maida.capture_setup import bound_hook_command

    other_field = "commandWindows" if maida_field == "command" else "command"
    mixed = {
        "type": "command",
        maida_field: bound_hook_command(receiver="codex-hook"),
        other_field: "other-required-command",
    }
    path = project / ".codex/hooks.json"
    path.parent.mkdir()
    original = {"hooks": {"Stop": [{"hooks": [mixed]}]}}
    path.write_text(json.dumps(original))
    initialize()
    configured = json.loads(path.read_text())
    assert configured["hooks"]["Stop"][0]["hooks"] == [mixed]
    assert len(configured["hooks"]["Stop"]) == 2
    result = runner.invoke(app, ["detach", "--agent", "codex"], input="y\n")
    assert result.exit_code == 0, result.output
    assert json.loads(path.read_text()) == original
    assert pointer(project)["providers"]["codex"]["enabled"] is False
