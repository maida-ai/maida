"""Executable package dependencies must remain acyclic."""

from tests.support.paths import REPO_ROOT
import subprocess
import sys
import pytest
from tests.support.dependencies import cyclic_edges, dependency_graph


@pytest.mark.parametrize("deferred", [False, True])
def test_dependency_check_detects_direct_and_deferred_cycles(tmp_path, deferred):
    package = tmp_path / "example"
    package.mkdir()
    (package / "__init__.py").write_text("from .a import value\n")
    (package / "a.py").write_text("from . import b\nvalue = 1\n")
    source = "from .a import value\n"
    if deferred:
        source = "def run():\n    from .a import value\n"
    (package / "b.py").write_text(source)
    assert cyclic_edges(dependency_graph(package)) == [("example.a", "example.b"), ("example.b", "example.a")]


def test_dependency_check_allows_annotations_but_checks_else_branch(tmp_path):
    package = tmp_path / "example"
    package.mkdir()
    (package / "a.py").write_text("from .b import value\n")
    (package / "b.py").write_text(
        "import typing\nfrom typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    from .a import Thing\n"
        "if typing.TYPE_CHECKING:\n    from .a import Other\n"
        "else:\n    from .c import value\n"
    )
    (package / "c.py").write_text("value = 1\n")
    graph = dependency_graph(package)
    assert graph["example.b"] == {"example.c"}
    assert cyclic_edges(graph) == []


def test_production_executable_imports_are_acyclic():
    package = REPO_ROOT / "maida"
    edges = cyclic_edges(dependency_graph(package))
    assert not edges, "Executable dependency cycles:\n" + "\n".join(f"{a} -> {b}" for a, b in edges)


def test_dependency_check_detects_package_facade_back_reference(tmp_path):
    package = tmp_path / "example"
    package.mkdir()
    (package / "__init__.py").write_text("from .a import run\nvalue = 1\n")
    (package / "a.py").write_text("from . import value\ndef run():\n    return value\n")
    assert cyclic_edges(dependency_graph(package)) == [("example", "example.a"), ("example.a", "example")]


def test_capture_recording_does_not_load_materialization():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import maida.config; import maida.capture.codex_hook; "
            "assert 'maida.project_local' not in sys.modules; "
            "assert 'maida.integrations.codex' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_claude_normalization_does_not_load_http_receiver():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import maida.integrations._claude_code.normalize; "
            "assert 'maida.capture.claude_code' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_langfuse_transport_does_not_load_normalization():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import maida.integrations._langfuse.client; "
            "assert 'maida.integrations._langfuse.normalize' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("reverse", [False, True])
def test_public_entrypoints_import_in_fresh_processes(reverse):
    modules = [
        "maida.gate",
        "maida.baseline_bind",
        "maida.policy",
        "maida.assertions",
        "maida.config",
        "maida.project_local",
        "maida.capture.codex_hook",
        "maida.integrations.codex",
        "maida.integrations.claude_code",
        "maida.integrations.langfuse",
        "maida.cli",
    ]
    if reverse:
        modules.reverse()
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib; " + "; ".join(f"importlib.import_module({module!r})" for module in modules),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
