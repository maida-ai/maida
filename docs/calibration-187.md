# Issue #187 calibration

These are measurements, not acceptance guarantees. The decision-rule grid uses a seeded Bernoulli source (`seed=187`) with 10,000 replications per cell and makes zero model calls. A cell below the policy validator boundary is shown as `load rejected`; it is not simulated.

False-fail is reported when the true pass rate is at or above θ. Missed regression is reported when it is below θ; because INCONCLUSIVE is provider-native and non-blocking, an inconclusive regression counts as missed for this measurement.

## Decision-rule grid

| N | θ | True pass rate | Status | False-fail | Inconclusive | Missed regression |
| ---: | ---: | ---: | --- | ---: | ---: | ---: |
| 1 | 0.70 | 0.99 | load rejected (n_min=7) | — | — | — |
| 1 | 0.70 | 0.95 | load rejected (n_min=7) | — | — | — |
| 1 | 0.70 | 0.90 | load rejected (n_min=7) | — | — | — |
| 1 | 0.70 | 0.85 | load rejected (n_min=7) | — | — | — |
| 1 | 0.70 | 0.70 | load rejected (n_min=7) | — | — | — |
| 1 | 0.80 | 0.99 | load rejected (n_min=11) | — | — | — |
| 1 | 0.80 | 0.95 | load rejected (n_min=11) | — | — | — |
| 1 | 0.80 | 0.90 | load rejected (n_min=11) | — | — | — |
| 1 | 0.80 | 0.85 | load rejected (n_min=11) | — | — | — |
| 1 | 0.80 | 0.70 | load rejected (n_min=11) | — | — | — |
| 1 | 0.90 | 0.99 | load rejected (n_min=25) | — | — | — |
| 1 | 0.90 | 0.95 | load rejected (n_min=25) | — | — | — |
| 1 | 0.90 | 0.90 | load rejected (n_min=25) | — | — | — |
| 1 | 0.90 | 0.85 | load rejected (n_min=25) | — | — | — |
| 1 | 0.90 | 0.70 | load rejected (n_min=25) | — | — | — |
| 3 | 0.70 | 0.99 | load rejected (n_min=7) | — | — | — |
| 3 | 0.70 | 0.95 | load rejected (n_min=7) | — | — | — |
| 3 | 0.70 | 0.90 | load rejected (n_min=7) | — | — | — |
| 3 | 0.70 | 0.85 | load rejected (n_min=7) | — | — | — |
| 3 | 0.70 | 0.70 | load rejected (n_min=7) | — | — | — |
| 3 | 0.80 | 0.99 | load rejected (n_min=11) | — | — | — |
| 3 | 0.80 | 0.95 | load rejected (n_min=11) | — | — | — |
| 3 | 0.80 | 0.90 | load rejected (n_min=11) | — | — | — |
| 3 | 0.80 | 0.85 | load rejected (n_min=11) | — | — | — |
| 3 | 0.80 | 0.70 | load rejected (n_min=11) | — | — | — |
| 3 | 0.90 | 0.99 | load rejected (n_min=25) | — | — | — |
| 3 | 0.90 | 0.95 | load rejected (n_min=25) | — | — | — |
| 3 | 0.90 | 0.90 | load rejected (n_min=25) | — | — | — |
| 3 | 0.90 | 0.85 | load rejected (n_min=25) | — | — | — |
| 3 | 0.90 | 0.70 | load rejected (n_min=25) | — | — | — |
| 5 | 0.70 | 0.99 | load rejected (n_min=7) | — | — | — |
| 5 | 0.70 | 0.95 | load rejected (n_min=7) | — | — | — |
| 5 | 0.70 | 0.90 | load rejected (n_min=7) | — | — | — |
| 5 | 0.70 | 0.85 | load rejected (n_min=7) | — | — | — |
| 5 | 0.70 | 0.70 | load rejected (n_min=7) | — | — | — |
| 5 | 0.80 | 0.99 | load rejected (n_min=11) | — | — | — |
| 5 | 0.80 | 0.95 | load rejected (n_min=11) | — | — | — |
| 5 | 0.80 | 0.90 | load rejected (n_min=11) | — | — | — |
| 5 | 0.80 | 0.85 | load rejected (n_min=11) | — | — | — |
| 5 | 0.80 | 0.70 | load rejected (n_min=11) | — | — | — |
| 5 | 0.90 | 0.99 | load rejected (n_min=25) | — | — | — |
| 5 | 0.90 | 0.95 | load rejected (n_min=25) | — | — | — |
| 5 | 0.90 | 0.90 | load rejected (n_min=25) | — | — | — |
| 5 | 0.90 | 0.85 | load rejected (n_min=25) | — | — | — |
| 5 | 0.90 | 0.70 | load rejected (n_min=25) | — | — | — |
| 7 | 0.70 | 0.99 | measured | 0.00% | 6.74% | — |
| 7 | 0.70 | 0.95 | measured | 0.00% | 30.69% | — |
| 7 | 0.70 | 0.90 | measured | 0.02% | 52.64% | — |
| 7 | 0.70 | 0.85 | measured | 0.17% | 67.25% | — |
| 7 | 0.70 | 0.70 | measured | 2.90% | 88.91% | — |
| 7 | 0.80 | 0.99 | load rejected (n_min=11) | — | — | — |
| 7 | 0.80 | 0.95 | load rejected (n_min=11) | — | — | — |
| 7 | 0.80 | 0.90 | load rejected (n_min=11) | — | — | — |
| 7 | 0.80 | 0.85 | load rejected (n_min=11) | — | — | — |
| 7 | 0.80 | 0.70 | load rejected (n_min=11) | — | — | — |
| 7 | 0.90 | 0.99 | load rejected (n_min=25) | — | — | — |
| 7 | 0.90 | 0.95 | load rejected (n_min=25) | — | — | — |
| 7 | 0.90 | 0.90 | load rejected (n_min=25) | — | — | — |
| 7 | 0.90 | 0.85 | load rejected (n_min=25) | — | — | — |
| 7 | 0.90 | 0.70 | load rejected (n_min=25) | — | — | — |
| 11 | 0.70 | 0.99 | measured | 0.00% | 10.39% | — |
| 11 | 0.70 | 0.95 | measured | 0.00% | 43.12% | — |
| 11 | 0.70 | 0.90 | measured | 0.04% | 68.74% | — |
| 11 | 0.70 | 0.85 | measured | 0.32% | 82.93% | — |
| 11 | 0.70 | 0.70 | measured | 7.85% | 90.05% | — |
| 11 | 0.80 | 0.99 | measured | 0.00% | 10.57% | — |
| 11 | 0.80 | 0.95 | measured | 0.02% | 43.07% | — |
| 11 | 0.80 | 0.90 | measured | 0.24% | 68.28% | — |
| 11 | 0.80 | 0.85 | measured | 1.45% | 81.45% | — |
| 11 | 0.80 | 0.70 | measured | — | 76.61% | 78.55% |
| 11 | 0.90 | 0.99 | load rejected (n_min=25) | — | — | — |
| 11 | 0.90 | 0.95 | load rejected (n_min=25) | — | — | — |
| 11 | 0.90 | 0.90 | load rejected (n_min=25) | — | — | — |
| 11 | 0.90 | 0.85 | load rejected (n_min=25) | — | — | — |
| 11 | 0.90 | 0.70 | load rejected (n_min=25) | — | — | — |
| 25 | 0.70 | 0.99 | measured | 0.00% | 0.04% | — |
| 25 | 0.70 | 0.95 | measured | 0.00% | 3.56% | — |
| 25 | 0.70 | 0.90 | measured | 0.00% | 23.54% | — |
| 25 | 0.70 | 0.85 | measured | 0.02% | 52.57% | — |
| 25 | 0.70 | 0.70 | measured | 4.49% | 92.31% | — |
| 25 | 0.80 | 0.99 | measured | 0.00% | 2.65% | — |
| 25 | 0.80 | 0.95 | measured | 0.00% | 35.09% | — |
| 25 | 0.80 | 0.90 | measured | 0.04% | 71.86% | — |
| 25 | 0.80 | 0.85 | measured | 0.80% | 89.94% | — |
| 25 | 0.80 | 0.70 | measured | — | 68.25% | 68.40% |
| 25 | 0.90 | 0.99 | measured | 0.00% | 22.44% | — |
| 25 | 0.90 | 0.95 | measured | 0.64% | 71.85% | — |
| 25 | 0.90 | 0.90 | measured | 10.19% | 82.63% | — |
| 25 | 0.90 | 0.85 | measured | — | 66.06% | 67.68% |
| 25 | 0.90 | 0.70 | measured | — | 9.10% | 9.10% |

## Recorded offline harness cost

The fixed-N=3 harness was repeated five times with one recorded synthetic LLM event and 42 recorded tokens per trial. It made no network calls. Runtime includes workspace isolation, trace persistence, aggregation, and fixed overhead.

| N | Gates | Calls/trial | Tokens/trial | Median calls/gate | Worst calls/gate | Median tokens/gate | Worst tokens/gate | Median wall/gate | Worst wall/gate |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3 | 5 | 1 | 42 | 3 | 3 | 126 | 126 | 1.533 s | 1.663 s |

## Injected-regression signature detectability

Detectability was checked at fixed N=3 with full sampling. Trial count changes confidence in a Bernoulli rate; it does not change whether the structural signature contains the injected signal.

| Injected regression | Signature signal | Detected at signature level |
| --- | --- | --- |
| Extra tool call | `tool_call_sequence` / `tool_call_counts` | yes |
| Missing stop condition | `final_status` and invariant outcome | yes |
| Step-count increase | per-trial `step_count` vector | yes |
