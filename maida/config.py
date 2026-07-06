"""Configuration for Maida: redaction, loop detection, guardrails, and data directory."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maida.guardrails import GuardrailParams
from maida.constants import LOCAL_DIR_NAME

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# Defaults (mirror env defaults)
_DEFAULT_REDACT = True
_DEFAULT_REDACT_KEYS = [
    "api_key",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
]
_DEFAULT_MAX_FIELD_BYTES = 20000
_DEFAULT_LOOP_WINDOW = 12
_DEFAULT_LOOP_REPETITIONS = 3

_MIN_MAX_FIELD_BYTES = 100
_MIN_LOOP_WINDOW = 4
_MIN_LOOP_REPETITIONS = 2


@dataclass
class MaidaConfig:
    """Runtime configuration for tracing, redaction, loop detection, and guardrails."""

    redact: bool
    redact_keys: list[str]
    max_field_bytes: int
    loop_window: int
    loop_repetitions: int
    data_dir: Path
    guardrails: GuardrailParams


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML from path. Return {} if file missing, invalid, or yaml unavailable."""
    if yaml is None:
        return {}
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _apply_yaml(config: dict[str, Any], key: str, default: Any) -> Any:
    """Get value from config dict if present and valid; else return default."""
    if key not in config:
        return default
    val = config[key]
    if key == "redact":
        return bool(val) if val is not None else default
    if key == "redact_keys":
        if isinstance(val, list) and all(isinstance(x, str) for x in val):
            return list(val)
        return default
    if key == "max_field_bytes":
        try:
            return max(_MIN_MAX_FIELD_BYTES, int(val))
        except (TypeError, ValueError):
            return default
    if key == "loop_window":
        try:
            return max(_MIN_LOOP_WINDOW, int(val))
        except (TypeError, ValueError):
            return default
    if key == "loop_repetitions":
        try:
            return max(_MIN_LOOP_REPETITIONS, int(val))
        except (TypeError, ValueError):
            return default
    if key == "data_dir":
        if val is None:
            return default
        return Path(val) if isinstance(val, (str, Path)) else default
    return default


def _guardrails_from_dict(data: dict[str, Any] | None) -> GuardrailParams:
    """Build GuardrailParams from a YAML guardrails section (user or project)."""
    if not data or not isinstance(data, dict):
        return GuardrailParams()
    stop_on_loop = bool(data.get("stop_on_loop", False))
    stop_on_loop_min_repetitions = 3
    try:
        stop_on_loop_min_repetitions = max(
            2, int(data.get("stop_on_loop_min_repetitions", 3))
        )
    except (TypeError, ValueError):
        pass
    max_llm_calls = None
    try:
        v = data.get("max_llm_calls")
        if v is not None:
            max_llm_calls = max(0, int(v))
    except (TypeError, ValueError):
        pass
    max_tool_calls = None
    try:
        v = data.get("max_tool_calls")
        if v is not None:
            max_tool_calls = max(0, int(v))
    except (TypeError, ValueError):
        pass
    max_events = None
    try:
        v = data.get("max_events")
        if v is not None:
            max_events = max(0, int(v))
    except (TypeError, ValueError):
        pass
    max_duration_s = None
    try:
        v = data.get("max_duration_s")
        if v is not None:
            max_duration_s = max(0.0, float(v))
    except (TypeError, ValueError):
        pass
    return GuardrailParams(
        stop_on_loop=stop_on_loop,
        stop_on_loop_min_repetitions=stop_on_loop_min_repetitions,
        max_llm_calls=max_llm_calls,
        max_tool_calls=max_tool_calls,
        max_events=max_events,
        max_duration_s=max_duration_s,
    )


def _apply_env_to_guardrails(params: GuardrailParams) -> GuardrailParams:
    """Override guardrail params from environment variables."""
    if "MAIDA_STOP_ON_LOOP" in os.environ:
        params = GuardrailParams(
            stop_on_loop=os.environ["MAIDA_STOP_ON_LOOP"].strip().lower()
            in ("1", "true", "yes"),
            stop_on_loop_min_repetitions=params.stop_on_loop_min_repetitions,
            max_llm_calls=params.max_llm_calls,
            max_tool_calls=params.max_tool_calls,
            max_events=params.max_events,
            max_duration_s=params.max_duration_s,
        )
    if "MAIDA_STOP_ON_LOOP_MIN_REPETITIONS" in os.environ:
        try:
            n = max(2, int(os.environ["MAIDA_STOP_ON_LOOP_MIN_REPETITIONS"]))
            params = GuardrailParams(
                stop_on_loop=params.stop_on_loop,
                stop_on_loop_min_repetitions=n,
                max_llm_calls=params.max_llm_calls,
                max_tool_calls=params.max_tool_calls,
                max_events=params.max_events,
                max_duration_s=params.max_duration_s,
            )
        except ValueError:
            pass
    if "MAIDA_MAX_LLM_CALLS" in os.environ:
        try:
            n = max(0, int(os.environ["MAIDA_MAX_LLM_CALLS"]))
            params = GuardrailParams(
                stop_on_loop=params.stop_on_loop,
                stop_on_loop_min_repetitions=params.stop_on_loop_min_repetitions,
                max_llm_calls=n,
                max_tool_calls=params.max_tool_calls,
                max_events=params.max_events,
                max_duration_s=params.max_duration_s,
            )
        except ValueError:
            pass
    if "MAIDA_MAX_TOOL_CALLS" in os.environ:
        try:
            n = max(0, int(os.environ["MAIDA_MAX_TOOL_CALLS"]))
            params = GuardrailParams(
                stop_on_loop=params.stop_on_loop,
                stop_on_loop_min_repetitions=params.stop_on_loop_min_repetitions,
                max_llm_calls=params.max_llm_calls,
                max_tool_calls=n,
                max_events=params.max_events,
                max_duration_s=params.max_duration_s,
            )
        except ValueError:
            pass
    if "MAIDA_MAX_EVENTS" in os.environ:
        try:
            n = max(0, int(os.environ["MAIDA_MAX_EVENTS"]))
            params = GuardrailParams(
                stop_on_loop=params.stop_on_loop,
                stop_on_loop_min_repetitions=params.stop_on_loop_min_repetitions,
                max_llm_calls=params.max_llm_calls,
                max_tool_calls=params.max_tool_calls,
                max_events=n,
                max_duration_s=params.max_duration_s,
            )
        except ValueError:
            pass
    if "MAIDA_MAX_DURATION_S" in os.environ:
        try:
            n = max(0.0, float(os.environ["MAIDA_MAX_DURATION_S"]))
            params = GuardrailParams(
                stop_on_loop=params.stop_on_loop,
                stop_on_loop_min_repetitions=params.stop_on_loop_min_repetitions,
                max_llm_calls=params.max_llm_calls,
                max_tool_calls=params.max_tool_calls,
                max_events=params.max_events,
                max_duration_s=n,
            )
        except ValueError:
            pass
    return params


def _user_config_dir() -> tuple[Path, Path]:
    """Return (xdg_base, legacy_base) for user config directories."""
    return Path.home() / ".config" / "maida", Path.home() / ".maida"


def load_config(project_root: Path | None = None) -> MaidaConfig:
    """
    Load MaidaConfig with precedence (highest first):
    1. Environment variables
    2. .maida/config.yaml in project root (if present)
    3. ~/.config/maida/config.yaml (falls back to ~/.maida/config.yaml)
    """
    xdg_base, legacy_base = _user_config_dir()
    redact = _DEFAULT_REDACT
    redact_keys = _DEFAULT_REDACT_KEYS.copy()
    max_field_bytes = _DEFAULT_MAX_FIELD_BYTES
    loop_window = _DEFAULT_LOOP_WINDOW
    loop_repetitions = _DEFAULT_LOOP_REPETITIONS
    data_dir = xdg_base

    # 3. User config - try XDG first, then legacy ~/.maida
    user_config_path = xdg_base / "config.yaml"
    user_cfg = _load_yaml(user_config_path)
    if not user_cfg:
        user_cfg = _load_yaml(legacy_base / "config.yaml")
    if user_cfg:
        redact = _apply_yaml(user_cfg, "redact", redact)
        redact_keys = _apply_yaml(user_cfg, "redact_keys", redact_keys)
        max_field_bytes = _apply_yaml(user_cfg, "max_field_bytes", max_field_bytes)
        loop_window = _apply_yaml(user_cfg, "loop_window", loop_window)
        loop_repetitions = _apply_yaml(user_cfg, "loop_repetitions", loop_repetitions)
        data_dir = _apply_yaml(user_cfg, "data_dir", data_dir)

    guardrails = GuardrailParams()
    if user_cfg and "guardrails" in user_cfg:
        guardrails = _guardrails_from_dict(user_cfg.get("guardrails"))

    # 2. Project config (overrides user)
    # TODO: `cwd()` might not be the best default for CLI root:
    #       If the tool is called from another location, CWD
    #       will not set the root to the project, but the place
    #       where CLI was called from.
    root = project_root if project_root is not None else Path.cwd()
    project_config_path = root / LOCAL_DIR_NAME / "config.yaml"
    proj_cfg = _load_yaml(project_config_path)
    if proj_cfg:
        redact = _apply_yaml(proj_cfg, "redact", redact)
        redact_keys = _apply_yaml(proj_cfg, "redact_keys", redact_keys)
        max_field_bytes = _apply_yaml(proj_cfg, "max_field_bytes", max_field_bytes)
        loop_window = _apply_yaml(proj_cfg, "loop_window", loop_window)
        loop_repetitions = _apply_yaml(proj_cfg, "loop_repetitions", loop_repetitions)
        data_dir = _apply_yaml(proj_cfg, "data_dir", data_dir)
        if "guardrails" in proj_cfg:
            guardrails = _guardrails_from_dict(proj_cfg.get("guardrails"))

    # 1. Env overrides (only when the key is explicitly set in the environment)
    if "MAIDA_REDACT" in os.environ:
        redact = os.environ["MAIDA_REDACT"].strip().lower() in ("1", "true", "yes")

    if "MAIDA_REDACT_KEYS" in os.environ:
        env_keys = os.environ["MAIDA_REDACT_KEYS"]
        redact_keys = [k.strip() for k in env_keys.split(",") if k.strip()]

    if "MAIDA_MAX_FIELD_BYTES" in os.environ:
        try:
            max_field_bytes = max(
                _MIN_MAX_FIELD_BYTES, int(os.environ["MAIDA_MAX_FIELD_BYTES"])
            )
        except ValueError:
            pass

    if "MAIDA_LOOP_WINDOW" in os.environ:
        try:
            loop_window = max(_MIN_LOOP_WINDOW, int(os.environ["MAIDA_LOOP_WINDOW"]))
        except ValueError:
            pass

    if "MAIDA_LOOP_REPETITIONS" in os.environ:
        try:
            loop_repetitions = max(
                _MIN_LOOP_REPETITIONS, int(os.environ["MAIDA_LOOP_REPETITIONS"])
            )
        except ValueError:
            pass

    if "MAIDA_DATA_DIR" in os.environ:
        env_data = os.environ["MAIDA_DATA_DIR"].strip()
        if env_data:
            data_dir = Path(env_data).expanduser()

    guardrails = _apply_env_to_guardrails(guardrails)

    return MaidaConfig(
        redact=redact,
        redact_keys=redact_keys,
        max_field_bytes=max_field_bytes,
        loop_window=loop_window,
        loop_repetitions=loop_repetitions,
        data_dir=data_dir,
        guardrails=guardrails,
    )
