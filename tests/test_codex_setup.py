"""Safe native Codex setup; unrelated hooks and trust remain untouched."""

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
    assert "leave the session open" not in result.output
    assert "run codex" in result.output.lower()
    assert "exit Codex" in result.output
    assert result.output.index("exit Codex") < result.output.index("maida check")
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


def test_dual_provider_attach_preserves_identity_and_capture_store(project):
    initialize("claude-code")
    old = pointer(project)
    store = load_config(capture=True).data_dir
    initialize()
    attached = pointer(project)
    assert attached["version"] == 2
    assert attached["project_id"] == old["project_id"]
    assert attached["providers"] == {"claude-code": {"enabled": True}, "codex": {"enabled": True}}
    assert load_config(capture=True).data_dir == store
    assert runner.invoke(app, ["init"]).exit_code == 2


@pytest.mark.parametrize("command", ["init", "detach"])
def test_setup_help_lists_both_capture_providers(project, command):
    result = runner.invoke(app, [command, "--help"], color=False)
    assert result.exit_code == 0
    assert "claude-code or codex" in result.output


@pytest.mark.parametrize("flag", ["--github", "--reviewed", "--from-run"])
def test_codex_init_combined_gate_flags_preserve_selected_provider(project, flag):
    args = ["init", "--agent", "codex", flag]
    if flag == "--from-run":
        args.append("latest")
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    assert "maida init --agent codex separately" in result.output
    assert "--agent claude-code" not in result.output
    assert not (project / ".maida").exists()


@pytest.mark.parametrize("agent", [None, "claude-code", "codex"])
def test_detach_permission_error_preserves_provider_selection(project, monkeypatch, agent):
    def fail(_agent):
        raise OSError("unreadable fixture")

    monkeypatch.setattr("maida._cli.setup.detach_capture", fail)
    args = ["detach", *(["--agent", agent] if agent else [])]
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    retry = "maida detach" + (f" --agent {agent}" if agent else "")
    assert f"rerun {retry}." in result.output
    if agent != "claude-code":
        assert "--agent claude-code" not in result.output


@pytest.mark.parametrize("uv", [False, True])
def test_codex_init_retry_preserves_provider_when_detection_is_ambiguous(project, monkeypatch, uv):
    monkeypatch.delenv("UV_RUN_RECURSION_DEPTH", raising=False)
    if uv:
        monkeypatch.setenv("UV_RUN_RECURSION_DEPTH", "1")
    retry = "uv run maida" if uv else "maida"
    (project / ".claude").mkdir()
    declined = initialize(answer="n\n")
    assert f"Rerun {retry} init --agent codex when ready" in declined.output
    monkeypatch.setattr("maida.first_run.is_interactive", lambda: False)
    preview = runner.invoke(app, ["init", "--agent", "codex"])
    assert preview.exit_code == 2
    assert f"Rerun {retry} init --agent codex in an interactive terminal" in preview.output
    assert not (project / ".maida/local.json").exists()


def test_codex_detach_and_reattach_preserve_identity_evidence_and_other_hooks(project):
    path = project / ".codex/hooks.json"
    path.parent.mkdir()
    other = {"hooks": [{"type": "command", "command": "team-observer"}]}
    path.write_text(json.dumps({"description": "Team hooks", "hooks": {"Stop": [other]}}))
    initialize("claude-code")
    claude = (project / ".claude/settings.local.json").read_bytes()
    initialize()
    original = pointer(project)
    evidence = load_config(capture=True).data_dir / "retained.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("saved evidence")
    assert runner.invoke(app, ["detach", "--agent", "codex"], input="y\n").exit_code == 0
    assert not pointer(project)["providers"]["codex"]["enabled"]
    detached_files = [path, project / ".maida/local.json", project / ".git/info/exclude"]
    before = [item.read_bytes() for item in detached_files]
    repeated = runner.invoke(app, ["detach", "--agent", "codex"])
    assert repeated.exit_code == 0
    assert "[y/N]" not in repeated.output
    assert [item.read_bytes() for item in detached_files] == before
    initialize()
    assert pointer(project) == original
    assert evidence.read_text() == "saved evidence"
    assert (project / ".claude/settings.local.json").read_bytes() == claude
    restored = json.loads(path.read_text())
    assert restored["description"] == "Team hooks"
    assert restored["hooks"]["Stop"].count(other) == 1
    assert len(restored["hooks"]["Stop"]) == 2
    before = [item.read_bytes() for item in detached_files]
    initialize(answer="")
    assert [item.read_bytes() for item in detached_files] == before


@pytest.mark.parametrize("agent", ["codex"])
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
    result = runner.invoke(app, ["init", "--agent", "codex"])
    assert result.exit_code == 2 and "restored" in result.output
    assert not (project / ".maida/local.json").exists()
    assert path.read_text() == '{"description":"concurrent"}'


@pytest.mark.parametrize("agent", ["codex"])
def test_detach_codex_keeps_claude_and_saved_evidence(project, agent):
    initialize("claude-code")
    claude = (project / ".claude/settings.local.json").read_bytes()
    initialize()
    evidence = load_config(capture=True).data_dir / "retained.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("saved evidence")
    result = runner.invoke(app, ["detach", "--agent", agent], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Codex capture" in result.output
    assert (project / ".claude/settings.local.json").read_bytes() == claude
    assert evidence.read_text() == "saved evidence"
    states = pointer(project)["providers"]
    assert states["claude-code"]["enabled"]
    assert not states["codex"]["enabled"]
    assert not json.loads((project / ".codex/hooks.json").read_text()).get("hooks")
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
    for path in (".maida/local.json",):
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


def test_exact_first_report_events_and_trust_state_are_preserved(project):
    home = Path.home() / ".codex"
    home.mkdir()
    config = home / "config.toml"
    config.write_text('[projects."/existing"]\ntrust_level="trusted"\n')
    trust = home / "hooks-trust.json"
    trust.write_text('{"existing":"trusted"}')
    before = [config.read_bytes(), trust.read_bytes()]
    initialize()
    hooks = json.loads((project / ".codex/hooks.json").read_text())["hooks"]
    assert set(hooks) == {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Stop",
        "Interrupt",
        "SessionEnd",
    }
    initialize(answer="")
    assert runner.invoke(app, ["detach", "--agent", "codex"], input="y\n").exit_code == 0
    assert before == [config.read_bytes(), trust.read_bytes()]
    assert not (project / ".agents").exists()
    assert not (project / ".maida/plugins").exists()


def test_claude_codex_ambiguity_requires_explicit_selection(project):
    (project / ".claude").mkdir()
    (project / ".codex").mkdir()
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 2
    assert "Ambiguous" in result.output
    assert "--agent claude-code" in result.output and "--agent codex" in result.output
    assert not (project / ".maida").exists()


@pytest.mark.parametrize("windows", [False, True])
def test_bound_codex_command_round_trips_shell_sensitive_interpreter(monkeypatch, windows):
    import maida.capture_setup as setup

    executable = (
        r"C:\Maida's tools $&;`\python.exe" if windows else str(Path("/tmp/Maida's tools $&;`/python").absolute())
    )
    monkeypatch.setattr(setup, "_WINDOWS", windows)
    monkeypatch.setattr(setup.sys, "executable", executable)
    command = setup.bound_hook_command("codex-hook")
    assert setup.hook_arguments(command) == [executable, "-E", "-P", "-m", "maida.cli", "capture", "codex-hook"]
    from maida.codex_setup import merged_hooks

    settings, _ = merged_hooks({}, command)
    hook = settings["hooks"]["Stop"][0]["hooks"][0]
    assert hook["command"] == command
    if windows:
        assert hook["commandWindows"] == command
