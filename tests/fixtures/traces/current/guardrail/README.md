# guardrail

Proves guardrail/error projection. The completed root span has an `exception`
event with `maida.error_type: GuardrailExceeded`, which baseline and assertion
logic classify as a guardrail event.
