import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas" / "trace" / "0.2.0"
FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "traces"
    / "external"
    / "emitter"
    / "current"
    / "multithread"
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_published_schemas_accept_external_fixture() -> None:
    meta_schema = _read_json(SCHEMA_DIR / "meta.schema.json")
    span_schema = _read_json(SCHEMA_DIR / "span.schema.json")
    Draft202012Validator.check_schema(meta_schema)
    Draft202012Validator.check_schema(span_schema)
    meta_validator = Draft202012Validator(meta_schema, format_checker=FormatChecker())
    span_validator = Draft202012Validator(span_schema, format_checker=FormatChecker())

    assert list(meta_validator.iter_errors(_read_json(FIXTURE / "meta.json"))) == []
    for line in (FIXTURE / "spans.jsonl").read_text(encoding="utf-8").splitlines():
        assert list(span_validator.iter_errors(json.loads(line))) == []


def test_unversioned_schema_names_are_current_aliases() -> None:
    assert _read_json(ROOT / "schemas" / "run.schema.json")["$ref"] == (
        "trace/0.2.0/meta.schema.json"
    )
    assert _read_json(ROOT / "schemas" / "event.schema.json")["$ref"] == (
        "trace/0.2.0/span.schema.json"
    )
