"""Regression tests for import placement in production code."""

from __future__ import annotations

import ast
from pathlib import Path


_ALLOWED_FUNCTION_IMPORTS = {
    (
        "maida/_tracing/_context.py",
        "_finalize_implicit_run",
        "from",
        "opentelemetry",
        ("context",),
    ),  # Detach only if an implicit run was activated.
    (
        "maida/_tracing/_context.py",
        "_ensure_run",
        "from",
        "maida._tracing._otel",
        ("_setup_otel", "_get_tracer"),
    ),  # Avoid import cycle during normal tracing module import.
    (
        "maida/_tracing/_context.py",
        "_ensure_run",
        "from",
        "opentelemetry",
        ("context",),
    ),  # Only needed when MAIDA_IMPLICIT_RUN creates an OTel context.
    (
        "maida/_tracing/_context.py",
        "_ensure_run",
        "from",
        "opentelemetry.trace.propagation",
        ("set_span_in_context",),
    ),  # Only needed when MAIDA_IMPLICIT_RUN creates an OTel context.
    (
        "maida/_tracing/_otel.py",
        "_setup_otel",
        "from",
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        ("OTLPSpanExporter",),
    ),  # Optional exporter dependency path, loaded only when configured.
    (
        "maida/_tracing/_otel.py",
        "_shutdown_otel",
        "import",
        "opentelemetry.trace",
        ("opentelemetry.trace",),
    ),  # Reset OTel module internals for test isolation.
    (
        "maida/cli.py",
        "view_cmd",
        "import",
        "uvicorn",
        ("uvicorn",),
    ),  # Viewer server dependency is only needed for `maida view`.
}


def _scope_for(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    scopes: list[str] = []
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append(parent.name)
        parent = parents.get(parent)
    return "/".join(reversed(scopes)) if scopes else None


def _import_key(
    path: Path,
    node: ast.Import | ast.ImportFrom,
    scope: str,
) -> tuple[str, str, str, str, tuple[str, ...]]:
    relpath = path.as_posix()
    if isinstance(node, ast.Import):
        modules = tuple(alias.name for alias in node.names)
        module = modules[0] if len(modules) == 1 else ",".join(modules)
        return (relpath, scope, "import", module, modules)

    module = "." * node.level + (node.module or "")
    names = tuple(alias.name for alias in node.names)
    return (relpath, scope, "from", module, names)


def test_function_scoped_imports_are_explicitly_justified() -> None:
    """Keep imports at module top unless a local import is intentional."""
    unexpected: list[tuple[str, str, str, str, tuple[str, ...]]] = []

    for path in sorted(Path("maida").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            scope = _scope_for(node, parents)
            if scope is None:
                continue
            key = _import_key(path, node, scope)
            if key not in _ALLOWED_FUNCTION_IMPORTS:
                unexpected.append(key)

    assert unexpected == []
