"""Assertion engine: policy checks and result aggregation.

Private implementation behind ``maida.assertions``. Sibling modules hold the
policy/result types and the evaluation engine. Import the public API from
``maida.assertions``. Report formatting lives in ``maida.report``. This
``__init__`` re-exports nothing so importing a leaf submodule cannot pull the
full package or create package-init cycles.
"""
