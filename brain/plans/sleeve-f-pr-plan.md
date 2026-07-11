# Sleeve F — PR / Commit Plan for `feat/optinet-sleeve-f`

**Date:** 2026-07-10
**Branch:** `feat/optinet-sleeve-f` (off `main`, ahead of d735121)
**Verdict:** Work worth keeping, not commit-ready as one blob. Split into 4 commits.
**Test status:** 51 sleeve-f/optinet tests pass. Full suite broken by an unrelated `duckdb` import — do not block on it, but note it in the PR body.

---

## Pre-commit fixes (must land before the corresponding commit)

1. **CI label fix** — current bootstrap reports 5th percentile of 1,000 day-cluster draws as "95% CI"; it is a **90% CI**. Either relabel or upgrade to spec (20-day blocks × 10k draws). Blocks commit 2.
2. **DSR k fix** — use cumulative trial count k ≥ 88 across all grids, not per-grid k (36/24/28). Blocks commit 2.
3. **`committed_grid` honesty** — runner writes `committed_grid: true` without checking git; both new grid configs are untracked. Either make the runner verify git-tracked status or strip the flag from existing summaries. Blocks commits 3–4.
4. **Strip false `sealed` claims** — holdout was touched three times (phase2, extended, phase3). Summaries/docs must not claim a sealed test. Blocks commit 4.

## Commit 1 — Data ingest + feed QA

**Scope:** `ingest/optinet_data.py`, `evidence/sleeve_f_real.py` (feed portions), `tests/test_optinet_data_normalization.py`, `tests/test_sleeve_f_real_feed.py`

- Futures normalization + feed QA.
- Zero-OI / zero-volume row handling (48 zero-OI, 666 zero-vol of 466k) — this was the crash root-cause from phase2 attempts 1 and 3.
- Tests included.

## Commit 2 — Replay + stats infrastructure

**Scope:** `evidence/stats.py`, `evidence/__init__.py`, remaining `evidence/sleeve_f_real.py`, `tests/test_sleeve_f_real_feed.py` (stats parts)

- Conservative replay: next-bar-open fills, OHLC first-touch, stop-wins ambiguity handling.
- Bootstrap CI + DSR — **only after fixes 1 and 2 above**.

## Commit 3 — Signal families + grid configs

**Scope:** `features/sleeve_f.py`, `features/sleeve_f_signals.py`, `configs/sleeve_f_grid_extended.json`, `configs/sleeve_f_grid_phase3.json`, `scripts/run_optinet_sleeve_f_full.py`, `scripts/run_optinet_sleeve_f_real.py`, `tests/test_optinet_sleeve_f_runner.py`, `tests/test_sleeve_phase_runners.py`, `tests/test_sleeve_f_phase3_signals.py`

- Both grid config JSONs are currently **untracked — commit them**; they are the pre-registration record.
- Runner changes.

## Commit 4 — Autopsy artifact

**Scope:** compact summary artifact + provenance note

- Title: **"new rule grids; original LightGBM not evaluated"**.
- Compact config-level results (per-config EV, CI-low with corrected 90% label, net-win rate, DSR with k ≥ 88) + checksums of the raw summaries.
- One provenance note covering the crash dirs (`attempt1-crash/`, `attempt3-crash/`: root cause = zero-OI/volume rows, fixed in commit 1) — then **drop the dirs**.

## Do NOT commit

- **165 MB raw summaries**: phase2 84 MB, extended 14 MB, phase3 67 MB (`runs/optinet-sleeve-f-full-phase2/`, `runs/optinet-sleeve-f-extended/`, `runs/optinet-sleeve-f-phase3/` raw output). Checksums go in commit 4; raw stays out of git. Add to `.gitignore` if not already covered.
- Crash dirs (`attempt1-crash/`, `attempt3-crash/`) — replaced by provenance note.
- `DONE.sentinel`, run logs (`run.log`, `stderr.log`, deleted `stdout.log`) unless repo convention says otherwise.

## PR framing

- Title the PR as **rule-family autopsy + infrastructure**, not as a Sleeve F implementation — the original LightGBM router was never evaluated (`models/lightgbm.py` is a stub).
- PR body must state: holdout spent ×3, global k ≥ 88 recorded, CI label corrected, `beats_baselines: false` means "not evaluated" (runner passes no promotion flags), and 2025 data gap (zero days; original OOS window unreproducible as-is).
- Note the unrelated `duckdb` import breaking the full test suite.
- Link to `brain/plans/sleeve-f-joint-forward-plan.md` for next steps.

## Order of operations

1. Apply pre-commit fixes 1–2 (stats), run sleeve-f tests.
2. Apply fixes 3–4 (honesty flags), regenerate/patch summaries.
3. Build compact autopsy artifact + checksums; write provenance note; delete crash dirs.
4. Stage and commit in the 4-commit order above (each commit's tests pass standalone).
5. Update `.gitignore` for raw run outputs.
6. Open PR against `main` with framing above.
