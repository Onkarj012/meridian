# Sleeve F — Campaign 1 session-quality dispositions (2020–2023), reviewed verdict

**Input:** `runs/sleeve-f-session-quality/` scan (994 sessions, 769 flagged). Proposals reviewed and amended as follows; this document is the binding disposition table for the freeze. Zero k — no outcome data consulted.

## Reviewed rulings (amendments to the scan's proposals)

1. **376-bar sessions (757):** the 15:30 bar is NSE's standard closing print, not a vendor error (scan's cause label corrected). Disposition unchanged in effect: **trim to 09:15–15:29**. The 15:30 bar is never a decision bar, never a feature input, never a label exit. This matches the incumbent 375-bar convention and the archive-parity range.
2. **Muhurat evening sessions (2020-11-14, 2021-11-04, 2022-10-24, 2023-11-12; 60–61 bars at 18:15–19:15):** scan proposed keep_full; **overruled → exclude_training**. Evening-session microstructure is not the regular-session process; 2024+ Muhurat sessions are absent from the archive, so exclusion also keeps eras comparable. (Eligibility warm-up would have zeroed them out regardless; exclusion makes it deterministic.)
3. **2021-02-24 (NSE outage; halt from 11:41, extension to 16:59; 221 bars): drop_day** — scan proposal accepted. Halt-plus-extension microstructure is non-representative and the extended tail is outside the registered session.
4. **Intra-session gap sessions** — 2020-03-13 (COVID lower-circuit halt 09:22–10:20), 2020-03-23 (halt 09:59–10:56), 2023-06-23 (14:16–14:57), 2023-10-26 (15:24–15:26), and the 2-minute-gap sessions 2022-01-13, 2022-06-28, 2023-09-12: scan's blanket "exclude_labels_near_close" **replaced by the contiguity rule below**. Causes on the 2020 dates corrected to exchange halts, not vendor truncation.

## Registered contiguity rule (subsumes rulings 3–4's label handling)

> A decision bar is eligible only if its full potential label window — next-bar entry through `min(60 bars, last same-session regular bar)` — is **contiguous** in the archive (no missing minutes). Decision bars inside or immediately before a gap are ineligible; label horizons never span missing minutes; missing bars are never imputed.

This one rule handles exchange halts, vendor gaps, and the 2–3-minute micro-gaps identically and deterministically, and it is implementable point-in-time (a gap is observable at decision time only insofar as bars up to *t* are missing; the forward-window check is applied in training-data construction and replay identically).

## Net effect on the training panel

- 757 sessions trimmed by one terminal bar (no row loss in the eligible range).
- 4 Muhurat sessions excluded (≈0 eligible rows anyway).
- 1 session dropped (2021-02-24).
- 7 gap sessions retained with gap-adjacent decisions ineligible per the contiguity rule.

These join the protocol §1c exclusions (2025-09-26, 2026-06-03 dropped; 2026-06-01/04 spot-gap NaN policy) in the freeze document.
