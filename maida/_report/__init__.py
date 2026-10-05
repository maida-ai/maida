"""Gate and trial report presentation.

Private implementation behind ``maida.report``. Sibling modules format
assertion results, trial/drift reports, structural diffs, and calibration
grids. Import the public API from ``maida.report``. This ``__init__``
re-exports nothing so importing a leaf submodule cannot pull the full
package or create package-init cycles.
"""
