"""Static executable-import graph, including deferred and relative imports.

Package initializers are scanned as modules. Implicit ancestor initialization is
not an edge: importing a child while its parent initializes is normal Python
behavior. Literal submodule imports through packages are resolved explicitly.
Annotation-only TYPE_CHECKING branches are excluded.
"""

import ast
from pathlib import Path


class ExecutableImports(ast.NodeVisitor):
    def __init__(self):
        self.imports = []

    def visit_If(self, node):
        test = node.test
        annotation_only = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute)
            and isinstance(test.value, ast.Name)
            and test.value.id == "typing"
            and test.attr == "TYPE_CHECKING"
        )
        if annotation_only:
            for child in node.orelse:
                self.visit(child)
        else:
            self.generic_visit(node)

    def visit_Import(self, node):
        self.imports.append(node)

    def visit_ImportFrom(self, node):
        self.imports.append(node)


def dependency_graph(package: Path) -> dict[str, set[str]]:
    files = {
        ".".join((package.name, *path.relative_to(package).with_suffix("").parts)).removesuffix(".__init__"): path
        for path in package.rglob("*.py")
    }
    graph = {module: set() for module in files}
    for module, path in files.items():
        visitor = ExecutableImports()
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        for node in visitor.imports:
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            else:
                base = node.module or ""
                if node.level:
                    parent = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
                    parts = parent.split(".")
                    base = ".".join(parts[: len(parts) - node.level + 1] + ([base] if base else []))
                targets = [f"{base}.{alias.name}" for alias in node.names]
                if any(target not in files for target in targets):
                    targets.append(base)
            graph[module].update(target for target in targets if target in files and target != module)
    return graph


def cyclic_edges(graph: dict[str, set[str]]) -> list[tuple[str, str]]:
    """Return every edge within a strongly connected group, deterministically."""
    indices = {}
    low = {}
    stack = []
    active = set()
    edges = []

    def visit(module):
        indices[module] = low[module] = len(indices)
        stack.append(module)
        active.add(module)
        for dependency in sorted(graph[module]):
            if dependency not in indices:
                visit(dependency)
                low[module] = min(low[module], low[dependency])
            elif dependency in active:
                low[module] = min(low[module], indices[dependency])
        if low[module] == indices[module]:
            group = set()
            while True:
                dependency = stack.pop()
                active.remove(dependency)
                group.add(dependency)
                if dependency == module:
                    break
            if len(group) > 1:
                edges.extend((source, target) for source in group for target in graph[source] & group)

    for module in sorted(graph):
        if module not in indices:
            visit(module)
    return sorted(edges)
