"""Isolated trial execution and statistical gate reports.

Private implementation behind ``maida.runner``. Sibling modules hold trial
types, markdown report rendering, workspace isolation, and trial execution.
Import the public API from ``maida.runner``. This ``__init__`` re-exports
nothing so importing a leaf submodule cannot pull the full package or create
package-init cycles.
"""
