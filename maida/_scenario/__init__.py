"""Isolated, capture-backed scenario execution for headless Claude Code.

Private implementation behind ``maida.scenario``. Sibling modules hold the
scenario types, manifest loading/validation, and Claude process execution.
Import the public API from ``maida.scenario``. This ``__init__`` re-exports
nothing so importing a leaf submodule cannot pull the full package or create
package-init cycles.
"""
