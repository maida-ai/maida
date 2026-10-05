"""
Config precedence tests for Maida.

Verifies: env > project YAML > user YAML > built-in defaults.
Env vars override ONLY when explicitly set in os.environ.
Uses tmp_path and monkeypatch; no real FS or network.
"""

from pathlib import Path

import pytest

# All env keys the config loader inspects (clean slate for every test).
_ENV_KEYS = [
    "MAIDA_REDACT",
    "MAIDA_REDACT_KEYS",
    "MAIDA_MAX_FIELD_BYTES",
    "MAIDA_LOOP_WINDOW",
    "MAIDA_LOOP_REPETITIONS",
    "MAIDA_DATA_DIR",
    "MAIDA_STOP_ON_LOOP",
    "MAIDA_STOP_ON_LOOP_MIN_REPETITIONS",
    "MAIDA_MAX_LLM_CALLS",
    "MAIDA_MAX_TOOL_CALLS",
    "MAIDA_MAX_EVENTS",
    "MAIDA_MAX_DURATION_S",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure none of the MAIDA_* env vars leak into tests."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _write_yaml(directory: Path, content: str) -> Path:
    """Write a config.yaml inside *directory*/.maida/ and return the file path."""
    from maida.constants import LOCAL_DIR_NAME

    cfg_dir = directory / LOCAL_DIR_NAME
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "config.yaml"
    cfg_file.write_text(content, encoding="utf-8")
    return cfg_file


# ------------------------------------------------------------------
# 1. YAML wins when env is absent
# ------------------------------------------------------------------


def test_yaml_wins_when_env_missing(tmp_path, monkeypatch):
    """Project YAML overrides defaults when no env vars are set."""
    _write_yaml(
        tmp_path,
        ("redact: false\nmax_field_bytes: 123\nloop_window: 7\nloop_repetitions: 5\n"),
    )
    # Point home somewhere empty so user YAML is absent.
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    from maida.config import load_config

    cfg = load_config(project_root=tmp_path)

    assert cfg.redact is False
    assert cfg.max_field_bytes == 123
    assert cfg.loop_window == 7
    assert cfg.loop_repetitions == 5


# ------------------------------------------------------------------
# 2. Env overrides YAML when present
# ------------------------------------------------------------------


def test_env_overrides_yaml_when_present(tmp_path, monkeypatch):
    """Explicitly-set env var beats YAML value."""
    _write_yaml(tmp_path, "max_field_bytes: 123\n")
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setenv("MAIDA_MAX_FIELD_BYTES", "456")

    from maida.config import load_config

    cfg = load_config(project_root=tmp_path)

    assert cfg.max_field_bytes == 456


# ------------------------------------------------------------------
# 3. Built-in defaults when no YAML and no env
# ------------------------------------------------------------------


def test_defaults_only_when_no_yaml_no_env(tmp_path, monkeypatch):
    """With no YAML and no env, defaults apply."""
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    from maida.config import load_config

    cfg = load_config(project_root=tmp_path)

    assert cfg.redact is True
    assert cfg.max_field_bytes == 20_000
    assert cfg.loop_window == 12
    assert cfg.loop_repetitions == 3


# ------------------------------------------------------------------
# 4. Trust-killer: YAML redact=false actually disables redaction
# ------------------------------------------------------------------


def test_yaml_redact_off_disables_redaction(tmp_path, monkeypatch):
    """With YAML redact=0 and no env, _redact_and_truncate must NOT redact."""
    _write_yaml(tmp_path, "redact: false\n")
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    from maida.config import load_config
    from maida._tracing.redact import _redact_and_truncate

    cfg = load_config(project_root=tmp_path)
    assert cfg.redact is False

    sample = {"api_key": "sk-secret-1234", "data": "hello"}
    result = _redact_and_truncate(sample, cfg)

    # api_key must NOT be redacted because redact is off.
    assert result["api_key"] == "sk-secret-1234"
    assert result["data"] == "hello"
