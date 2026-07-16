# Sleeve F — Campaign 2 supersession

**Status:** `WITHDRAWN_UNEXECUTED`
**Decision date:** 2026-07-16
**Principal decision:** Onkar approved C2 supersession under Option 2: withdraw C2 unexecuted. No replacement implementation or outcome inspection is authorized by this record.

## Registered identity

- Conditional registration: `registrations/sleeve-f/c2/conditional-registration.md`.
- Conditional-registration freeze: C1/C2 registration commit `1f387b8`.
- C1 disposition: `registrations/sleeve-f/c1/decision.md`, commit `3043bae63cf0b796f8c4d407e46a985dbbf2723c`.
- C1 WF evidence: artifact commit `82533fb18856adcac2fa4f1a50ac5c861fb2e10b`; manifest SHA-256 `9a3704b4a87331c17b41f9867b116dfa683925f78c9434a2b20e067db630c7b9`.
- `conditional-registration.md` is unchanged.

## Branch and execution record

- C1’s registered decision tree selected **Branch B** because C1 yielded no candidate passing the registered gates.
- C2-W was registered and never run or inspected.
- C2-P was registered and never run or inspected.
- The four registered C2 baselines — time-of-day-only, volatility-only, unconditional-long, and random-entry — were never run or inspected.
- No C2 predictions, thresholds, labels, replay results, or candidate-relative outcomes were generated.
- Repository check: no C2 outcome directory exists under `runs/`. The only top-level `runs/sleeve-f-c2*` match is `runs/sleeve-f-c2-matrices/`, which is a non-outcome registered matrix/preparation directory; no C2 WF or outcome directory is present.

## Supersession basis

This is an outcome-informed supersession, not a zero-information correction. C1 WF evidence invalidated the shared long-only `+40/−30 bps` 60-bar label/replay mechanism: the C1 autopsy recorded negative gross PnL before costs, timeout-dominated realization, zero short trades, losses across every calendar year, and no candidate passing the registered WF gates. Executing C2 unchanged would preserve that invalidated mechanism while changing inputs and loss function.

C2 execution is therefore withdrawn before any C2 outcome is generated. This record distinguishes “pre-registered but withdrawn unexecuted” from “tested and failed.”

## Holdout and multiplicity controls

- C1 holdout: 2025-07-01 → 2026-06-30; accessed: `false`.
- The C1 holdout remains unopened and off-limits for C2 redesign, replacement development, geometry selection, or C2 validation.
- Declared ledger: `k = 101` (`k = 95` executed C1 outcomes + two C2 candidates + four C2 baselines).
- Executed-outcome ledger: `k = 95`; the six C2 configurations produced no inspected outcomes.
- Any replacement campaign starts after declared `k = 101`.

