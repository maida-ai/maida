"""Local run storage for OTel traces and legacy run directories.

Private implementation behind ``maida.storage``. Sibling modules hold path
helpers, run resolution, loaders, and writers. Import the public API from
``maida.storage``. This ``__init__`` re-exports nothing so importing a leaf
submodule cannot pull the full package or create package-init cycles.
"""
