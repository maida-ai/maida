# loop

Proves deterministic loop-signature derivation. The child span sequence repeats
`LLM_CALL:gpt-loop -> TOOL_CALL:lookup` three times and the root span also
contains a projected `LOOP_WARNING` event.
