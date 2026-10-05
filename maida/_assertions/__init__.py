"""Assertion engine: policy checks, result aggregation, and report formatting.

Private implementation behind ``maida.assertions``. Sibling modules hold the
policy/result types, the evaluation engine, and human/machine report
formatters. Import the public API from ``maida.assertions``. This ``__init__``
re-exports nothing so importing a leaf submodule cannot pull the full package
or create package-init cycles.
"""
