"""OTel-backed run tracing: context, lifecycle, and recorders.

Private implementation behind ``maida.tracing``. Captures agent runs as
OpenTelemetry traces with redaction, implicit-run support, and local span
export. Import the public API from ``maida.tracing`` (or ``maida``). This
``__init__`` re-exports nothing so importing a leaf submodule cannot pull the
full tracing stack or create package-init cycles.

Dependencies: stdlib + maida.config + maida.constants + maida.events +
maida.storage.

TODO(concurrency): Safe for single-threaded agent loops. If tools run
concurrently (e.g. thread pool), context does not propagate to worker threads
and the event window ordering can be non-deterministic. For v0.2+: propagate
context into workers (contextvars.copy_context().run(...)) and use a
thread-safe window (e.g. lock around appends) with a well-defined ordering
rule so loop detection remains meaningful.
"""
