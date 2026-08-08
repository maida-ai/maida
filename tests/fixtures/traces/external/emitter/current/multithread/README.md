# External emitter multi-thread trace

This sanitized fixture is authored directly in Maida's native trace format.
The run root and main model call form the main thread. The `delegate` tool span
starts a subthread; its model and `read` tool descendants preserve that branch
through `parent_span_id` relationships.

The fixture intentionally includes additive emitter-owned fields to prove that
compatible enrichment is accepted without adding emitter-specific event types.
