"""Canonical plan artifacts and typed plan evidence.

Private implementation behind ``maida.plan_contract``. Sibling modules hold
parsing helpers, plan artifacts, metric extraction, and pre-execution evidence.
Import the public API from ``maida.plan_contract``. This ``__init__`` re-exports
nothing so importing a leaf submodule cannot pull the full package or create
package-init cycles.
"""
