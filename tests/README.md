# Test organization

Tests mirror the implementation package, including private package names: `maida/_cli/` maps to `tests/_cli/`, `maida/capture/` maps to `tests/capture/`, and provider importer packages map to their counterparts beneath `tests/integrations/`. Tests for flat implementation modules and public facade compatibility remain at the test root. Mixed command tests are grouped by the command implementation that owns their behavior.

`tests/repository/` checks packaging, documentation, workflows, schemas, and dependency architecture. `tests/end_to_end/` exercises workflows spanning several implementation areas. These directories describe ownership; they do not change pytest markers or CI scheduling.

Keep trace fixtures in `tests/fixtures/` and reusable helpers in `tests/support/`. Import paths from `tests.support.paths` rather than relying on a test file's directory depth. Import helpers from support modules, never another test module or `conftest.py`. The root conftest owns global HOME, working-directory, storage, and OpenTelemetry isolation; package conftests provide only local fixtures.

Patch dependencies in the implementation module that resolves them, rather than a public facade that re-exports the function. Internal production modules should likewise import shared types from their defining modules rather than facades that also load orchestration.

Run the complete suite with `uv run pytest --cov`, or a package with `uv run pytest tests/_cli`. Run architecture checks with `uv run pytest tests/repository/test_dependencies.py tests/repository/test_import_placement.py`. The executable dependency check includes deferred imports and explicit package initializer dependencies; it excludes annotation-only `TYPE_CHECKING` imports. Dynamic imports and implicit ancestor package initialization are outside that static graph, so fresh-process import checks complement it.
