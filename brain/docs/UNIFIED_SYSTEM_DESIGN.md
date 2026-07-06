# MERIDIAN — Unified Stock Recommendation & Trading System

> **Design document, v1.0 — 2026-07-04**
> Synthesized from three audited systems: **StockXpert** (swing DL, `brain/stockxpert.md`), **Asterion** (intraday research platform, `brain/asterion.md`), **intranet_optinet** (futures router + equity recommenders, `brain/intranet_optinet.md`).
> Goal: one real, profitable, reliable stock recommendation system for Indian markets (NSE cash equities + index futures).

---

## 0. Executive Verdict — Which System to Continue

**Continue with Asterion as the platform base.** Do not start from scratch, and do not continue any of the three as-is.

| System | Keep | Kill |
|---|---|---|
| **Asterion** | The entire platform layer: data bedrock (bronze/silver/gold), source contracts, promotion gates, sealed-test discipline, evidence-driven signal ladder | Its current signal set (nothing promoted; broad signal is −10.77 bps EV after costs) |
| **intranet_optinet** | NIFTY futures router v0 (the only validated positive edge across all three repos: forward-walk Sharpe 2.17, 1,128 trades, +₹429k), the ops layer (daily cron, paper ledger, halt logic, risk governor, broker abstraction), the V8 label-economics diagnosis method | V8 ensemble (Sharpe −8.22), Recommender V2 (failed promotion, live output contradicts its own selection logic), archived options tracks |
| **StockXpert** | Cross-sectional splits + embargo + `assert_no_leakage`, stock-traits conditioning (not learned embeddings), isotonic confidence calibration, full Indian cost model, pre-registered walk-forward protocol, the V3 multi-encoder architecture *as a challenger only* | The strategy layer (pre-registered walk-forward: −64.3%, Sharpe −0.79, 6/6 folds negative, underperforms random entry), centered-smoothed labels as a headline metric, V4 scaffold (empty model file), light-cost headline numbers |

**Why Asterion as base:** it is the only repo whose *process* is designed to prevent exactly the failures the other two suffered. StockXpert built a strong model and then discovered — honestly, via pre-registration — that its strategy doesn't survive out-of-sample. Optinet built four systems and only the one with disciplined phase-gated validation (futures router) works. Asterion never shipped a false positive because its gates refused to promote weak evidence. The platform that refuses to lie to you is the platform you scale.

---

## 1. The Idea

Build **one system, three layers, multiple strategy "sleeves"**:

1. **A data bedrock** — contracted, point-in-time-correct, versioned market data for NSE (Asterion's architecture, extended with optinet's Kite pipelines and StockXpert's sentiment stack).
2. **An evidence engine** — a standing harness that takes any candidate signal (rule-based, GBM, deep) through an identical gauntlet: causal feature contracts → full-cost backtest → pre-registered walk-forward → promotion gates → sealed test. Signals are *promoted*, never *assumed*.
3. **An execution/recommendation layer** — a daily operational loop (cron, paper ledger, risk governor, halts, dashboard) that trades only promoted signals and publishes recommendations with calibrated confidence.

Strategy sleeves (independent, individually gated):

- **Sleeve F (Futures Router)** — port optinet's NIFTY futures intraday router as-is. It is already validated and live-paper-trading. It becomes the system's first *promoted* sleeve and the template for what "promoted" means.
- **Sleeve X (Cross-sectional Equity Swing)** — the flagship rebuild: daily-horizon long/short equity ranking over Nifty 100–200, using StockXpert's model ideas re-labeled with economically viable targets, run through Asterion's gates.
- **Sleeve M (Intraday Equity Momentum)** — Asterion's "alive but starved" momentum slice (EV +44 bps, PF 1.99, CI-low +9.15 bps on 97 trades), fed a bigger universe and longer history to reach gate-passing sample size.

The product output is a **daily recommendation set**: ranked picks with side, entry window, stop/target, calibrated probability, position size, and the evidence trail (which sleeve, which model version, which gates it passed).

---

## 2. The Thought Process

### 2.1 What the three systems collectively prove

1. **Predictive signal exists.** StockXpert's blind-holdout H1 direction accuracy is 70.2% (p = 3.18×10⁻⁶³, ECE 0.093) on *completely unseen stocks*. Optinet's futures router has real forward-walk edge. Asterion found a genuine momentum slice. The market is not efficient enough to give us nothing.
2. **Prediction ≠ profit. Every system died in the gap between them.**
   - StockXpert: 70% smoothed-trend accuracy → −64.3% pre-registered walk-forward P&L.
   - Optinet V8: direction AUC 0.59 (real, shuffled-delta +0.104) → Sharpe −8.22, because the barrier label (1.5% target / 1.0% stop) has a 24.47% natural hit rate against a 47.34% post-cost breakeven. **The label geometry was uneconomical before a single model was trained.**
   - Optinet Recommender V2: 61.6% backtest hit rate → 54.17% in 2025 → zero official picks in champion eval.
3. **Selection bias is the main enemy, and only pre-registration catches it.** StockXpert's walk-forward winners came "from a thin positive right tail of an otherwise losing grid." Its single-year +11.51% Conservative 2025 result is plausibly the same right-tail selection. Asterion's promotion gates rejected its momentum signal for exactly this reason (concentration, trade count) rather than shipping it.
4. **Simple beats deep, so far.** The only validated edge (futures router) is a LightGBM — actually a *single tree* dominated by `realized_vol_30m` and `minute_of_day`. Asterion's best slice is plain momentum. Deep models (V8, HybridSwingNet strategy layer, RegimeGraphAlphaNet) have not earned their complexity anywhere in these repos.
5. **Ops discipline is a separate skill and optinet already has it.** Daily cron, append-only paper ledger, hard/soft halt thresholds, kill-switch files, triple-key live gate. None of the other repos have a working operational loop.

### 2.2 Design principles derived from those lessons

- **P1 — Label economics before modeling.** Every target definition must pass a *label viability audit*: natural hit rate vs. post-cost breakeven win rate at realistic slippage, before any model trains on it. (Directly from optinet's `v8_diagnosis.json` method.)
- **P2 — Full Indian costs from day one, no light-cost mode.** StockXpert's +24.41% headline collapsed to +11.51% under full costs (~41 bps round-trip delivery) and to negative under walk-forward. The backtest engine has exactly one cost mode: full.
- **P3 — Evidence ladder with hard promotion gates.** Baselines (random, momentum, buy-and-hold) → linear/GBM → deep, each gated on: CI-low of expected value > 0 after costs, minimum trade count, minimum trading days, maximum symbol concentration, stability across folds (worst-fold metric, not mean). A signal that fails stays quarantined.
- **P4 — Pre-register, then seal.** Strategy-selection protocols are committed to git *before* out-of-sample execution (StockXpert's protocol). Final holdout periods are sealed and spendable once (Asterion's sealed test).
- **P5 — Point-in-time everything.** PIT index constituents (kills StockXpert's survivorship flaw), PIT FII/DII availability timestamps (kills Recommender V2's leakage risk), no proxy/actual instrument skew (kills the router's index-proxy weakness), trailing-only features enforced by contract, not convention.
- **P6 — Calibrated probabilities, selective execution.** StockXpert's isotonic calibration + confidence thresholding is the right interface between model and money: trade only high-conviction, sized by conviction, and report coverage honestly.
- **P7 — Prefer starving a good signal to feeding a bad one.** More data/universe for signals that are alive-but-starved (Sleeve M); no rescue attempts for signals that are structurally dead (V8).

---

## 3. Introduction

MERIDIAN is a research-first, promotion-gated trading system for Indian markets. It treats "recommendation" as the end of a pipeline whose every stage is contracted and audited:

```
raw sources → source contracts → bronze/silver/gold lake → causal features (label contracts)
→ candidate signals → evidence engine (backtest + walk-forward + gates) → promotion
→ paper trading → live gating → daily recommendations
```

Scope for v1:

- **Markets:** NSE cash equities (Nifty 100 → 500 expansion path) + NIFTY 50 index futures.
- **Horizons:** intraday (minutes–hours, Sleeves F and M) and swing (1–10 trading days, Sleeve X).
- **Direction:** long and short where instrument allows (futures both sides; cash equities long-biased, intraday short allowed).
- **Capital assumption:** retail/prop scale (₹10–50 L), Zerodha Kite cost structure, delivery + intraday cost models.
- **Non-goals for v1:** options strategies (optinet's options track is archived with a failed-audit postmortem — respect that), HFT/microstructure, US markets.

Success criteria (define them now, so results can't be re-framed later):

1. At least one sleeve promoted through all gates and paper-trading with ≥ 6 months, ≥ 200 trades, CI-low of per-trade EV > 0 after full costs.
2. Paper Sharpe ≥ 1.0 over that window with max drawdown ≤ 15%.
3. Live recommendation accuracy tracked daily against calibrated probabilities (ECE ≤ 0.10 realized).
4. Zero unexplained divergence between backtest fills and paper fills > agreed slippage budget.

---

## 4. Data Required

Principle: **every source enters through a source contract** (Asterion): schema, availability timestamp (when is this value actually knowable?), reconciliation checks, quarantine status. A source is *quarantined* until it demonstrably improves a gated signal — news/sentiment starts quarantined (as in Asterion), price/volume starts promoted.

### 4.1 Stock data (per-symbol, the core)

| Item | Granularity | Source | Notes / existing assets |
|---|---|---|---|
| OHLCV minute bars | 1-min | Zerodha Kite historical API | optinet already has ~531 Nifty 500 symbols, 2015→2026-06; its fetcher + token-refresh cron works |
| OHLCV daily bars | daily | Kite (primary), yfinance (fallback/reconciliation) | StockXpert has 2013→2026 daily cache; use as cross-check, not primary (yfinance pre-2018 quality unaudited) |
| Corporate actions | event | NSE bhavcopy / Kite | splits, bonuses, dividends — mandatory for adjusted-price correctness; reconcile adjusted vs raw |
| **Point-in-time index constituents** | monthly snapshots | NSE index factsheets (historical) | **New requirement.** All three systems back-applied today's universe — direct survivorship bias. Build a constituents-history table before any backtest is trusted |
| Delivery percentage | daily | NSE bhavcopy | proxy for conviction vs churn |
| Bulk/block deals | daily | NSE | event features |
| Corporate announcements/earnings calendar | event | NSE/BSE filings | Asterion has an `announcements.py` ingester; earnings-window flags are cheap and causal |
| Shareholding patterns (FII/DII/promoter %) | quarterly | NSE filings | slow-moving context features |

### 4.2 My market data (market-level, India)

| Item | Granularity | Source | Notes |
|---|---|---|---|
| NIFTY 50 / BANKNIFTY / sector indices | 1-min + daily | Kite | optinet has NIFTY 50 minutes 2015→2026 (volume column is zero — known defect, do not use index volume) |
| INDIA VIX | daily (+ intraday if available) | Kite/NSE | optinet has 2009→2026; used in router risk guards |
| **NIFTY futures actual prices + OI** | 1-min | Kite | **Fix the router's index-proxy skew**: it was trained on futures features but paper-trades on index proxy with OI/volume zeroed. Restore real futures feed |
| Market breadth (adv/dec, % above 50/200 DMA, new highs/lows) | daily | computed from universe | Asterion `breadth.py` + StockXpert regime scores both exist; unify |
| FII/DII flows | daily | NSE | **contract must record release time** (published after market close → usable only next day). Recommender V2's raw same-day columns were a leakage risk |
| Options chain snapshot (PCR, max-pain, IV skew) | EOD | NSE bhavcopy | context features only in v1; no options trading |
| Futures rollover/basis | daily | Kite | router already computes basis features |

### 4.3 News data

| Item | Source | Status |
|---|---|---|
| Per-stock news headlines | Google News RSS, yfinance news | quarantined until promoted |
| Market-wide news volume/tone | GDELT | StockXpert has GDELT backfill scripts (note: its 2026 Mar–Apr GDELT file is empty — regenerate); quarantined |
| Corporate filings text | NSE announcements | event flags promoted (binary "announcement today" is safe); NLP content quarantined |

### 4.4 Sentiment

| Item | Method | Existing asset |
|---|---|---|
| Daily per-stock sentiment score | FinBERT on headlines, TextBlob fallback | StockXpert `mdata/finbert.py` + combined CSVs 2015→2026 — port wholesale |
| Market-mood aggregate | mean/dispersion of per-stock scores + GDELT tone | build in silver layer |
| Positioning sentiment | FII/DII net flows, PCR | derived from 4.2 |

**Gate:** sentiment enters models only after an ablation shows CI-significant improvement on a promoted signal. Asterion quarantined it; StockXpert included it but its strategy failed anyway — neither repo has *evidence* sentiment earns its keep. Treat it as a hypothesis.

### 4.5 Macro — national

| Item | Frequency | Notes |
|---|---|---|
| RBI repo rate + policy dates | event | rate-decision-window flags |
| CPI/WPI inflation prints | monthly | surprise vs. consensus if obtainable, else level/change |
| USD/INR | daily | risk-on/off; Kite or RBI reference rate |
| Crude (Brent) | daily | India-specific macro sensitivity |
| 10Y G-sec yield | daily | equity-bond regime |
| GST collections, IIP, PMI | monthly | slow context; low priority |

### 4.6 Macro — international

| Item | Frequency | Notes |
|---|---|---|
| S&P 500 / NASDAQ overnight returns | daily | strongest known open-gap driver for NSE |
| SGX/GIFT Nifty pre-open | pre-market | direct gap predictor input (StockXpert has a `GapPredictor` module) |
| DXY, US 10Y | daily | flow direction context |
| FOMC/major central-bank calendar | event | volatility-window flags |
| Asian session (Nikkei, Hang Seng) | daily | pre-open context |

### 4.7 Everything else (metadata & infra data)

- **Symbol master** with ISIN, listing dates, lot sizes, tick sizes, circuit bands (Asterion has `symbol_master.json`).
- **Trading calendar** with muhurat/special sessions.
- **Cost tables** versioned like data: brokerage, STT (delivery 0.1%, intraday 0.025%), stamp, exchange txn, GST, SEBI fees, and a slippage model per liquidity bucket. StockXpert's ~41 bps delivery round-trip and optinet's per-trade ₹ figures are the starting points.
- **Data quality dashboards**: freshness, gap detection, reconciliation diffs (Kite vs yfinance daily closes), zero-volume detection (would have caught optinet's index volume defect immediately).

---

## 5. System Design

### 5.1 The lake (from Asterion, extended)

- **Bronze**: raw ingests, immutable, source-stamped (hash + snapshot manifest — Asterion's `source_snapshot.py`).
- **Silver**: cleaned, adjusted, reconciled series; corporate-action-adjusted prices; PIT constituent joins.
- **Gold**: causal feature/label tables with **label contracts** (JSON manifest describing horizon, target math, embargo, cost assumptions) — Asterion's `causal.label-contract.json` pattern. Storage: DuckDB + Parquet (already working in Asterion).

### 5.2 Labels (the most important design decision)

Two label families, both economically audited before use (P1):

1. **Tradable barrier labels** (Sleeves F, M): first-touch of target/stop within horizon, with geometry chosen so that *natural hit rate comfortably exceeds post-cost breakeven*. Run the V8-diagnosis audit for every (target, stop, horizon, cost) tuple and publish the viability table. V8's fatal 1.5%/1.0% geometry (24.5% natural vs 47.3% breakeven) is the canonical counter-example.
2. **Ranking labels** (Sleeve X): cross-sectional forward return rank (1–10 day horizon), *not* centered-smoothed direction. StockXpert's smoothed-target accuracy was real but untradable-as-advertised; if smoothing is used at all it must be trailing-only and the recommendation layer must translate to raw tradable outcomes. Primary Sleeve X metric: rank IC and top-K minus bottom-K net spread after costs — a quantity that is directly monetizable.

### 5.3 Features

- Trailing-only, enforced by gold-layer contract tests (grep-level guards for `center=True` / `shift(-` in feature code as CI checks — StockXpert's audit found these only in labels, but make it mechanical).
- Feature groups: price/returns (multi-window), volatility (ATR, Parkinson, realized), volume/liquidity, VWAP/opening-range (intraday), cross-sectional ranks (Recommender V2's Stage B design was sound), sector/index relative strength, breadth/regime, gap features (StockXpert `GapPredictor` inputs), calendar, and — behind quarantine — sentiment/news/positioning.
- **Stock identity via traits, not embeddings** (StockXpert's proven fix): sector, mcap bucket, beta, vol, liquidity, dividend yield, float. Confirmed to generalize (blind > test on all horizons).

### 5.4 Models — the evidence ladder

| Rung | Models | Purpose |
|---|---|---|
| 0 | Random, buy-and-hold, SMA crossover, plain momentum | baselines every candidate must beat; StockXpert's walk-forward lost to two of these — that check is non-negotiable |
| 1 | Ridge/logistic on gold features | linear evidence of signal |
| 2 | LightGBM (direction + quantile heads, isotonic-calibrated) | expected workhorse; every validated edge across all three repos is tree-based |
| 3 | StockXpert V3 multi-encoder (ResNLS/BiGRU/BiLSTM + traits + attention fusion) | *challenger only*; must beat rung 2 on gated, full-cost metrics on the identical protocol to earn a slot |

Rung 3 imports StockXpert's exact training discipline: cross-sectional stock splits with 10-day embargo, `assert_no_leakage`, train-only scaler fits, isotonic calibration on validation, **plus multi-seed runs** (StockXpert ran seed 42 only — fix that; report across ≥ 5 seeds).

### 5.5 Policy layer (model → trades)

- Calibrated probability → confidence-threshold execution (trade only above τ; τ selected inside walk-forward folds, never on OOS).
- Position sizing: fixed-fractional with conviction scaling, ADV participation cap (5%), sector cap (30%), max positions, drawdown-pause (15%) — union of StockXpert's and optinet's risk controls.
- Regime gate: causal breadth/trend classifier (StockXpert's `robust` regime policy) as an *evaluated* overlay — kept only if it improves worst-fold results.
- Selection metric for any policy grid: **worst-fold return** (StockXpert's post-mirage fix), never mean or best.

### 5.6 Promotion gates (from Asterion, hardened)

A signal is promoted only if, on pre-registered walk-forward, all of:

1. CI-low (block-bootstrap, 95%) of per-trade EV > 0 after full costs.
2. ≥ 300 trades across ≥ 100 trading days (Sleeve M was rejected at 97 trades — right call; scale data, not standards).
3. Max single-symbol trade share ≤ 20%; ≥ 20 distinct symbols (equity sleeves).
4. Positive in ≥ 70% of folds; worst fold above a floor.
5. Beats all rung-0 baselines on net Sharpe.
6. Deflated Sharpe Ratio > 0 given the *full* number of configurations ever evaluated (StockXpert computed DSR(k=144) — keep an honest global k counter).
7. Sealed test (untouched final period) spent once, passes 1–5.

---

## 6. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        MERIDIAN MONOREPO                        │
│                                                                 │
│  ingest/          Kite client, yfinance fallback, NSE bhavcopy, │
│                   GDELT/news, macro fetchers, token-refresh cron│
│  contracts/       source contracts, label contracts,            │
│                   quarantine registry, PIT availability stamps  │
│  lake/            bronze → silver → gold (DuckDB + Parquet)     │
│  features/        causal builders per sleeve + trait vectors    │
│  models/          rung 0–3; training loops; calibration         │
│  evidence/        backtest engine (full-cost only),             │
│                   walk-forward runner, bootstrap/DSR stats,     │
│                   promotion gate evaluator, sealed-test vault   │
│  policy/          thresholds, sizing, regime gate, portfolio    │
│  ops/             daily cron DAG, paper ledger (append-only),   │
│                   risk governor, halt/kill-switch files,        │
│                   broker adapter (paper → Kite live behind      │
│                   triple-key gate), drift monitor               │
│  serve/           FastAPI recommendations API + dashboard       │
│  registry/        model versions, run manifests, config hashes  │
└─────────────────────────────────────────────────────────────────┘
```

Provenance of each component:

| Component | Ported from | State |
|---|---|---|
| Lake + contracts + gates + sealed tests | Asterion | working, port structurally |
| Kite ingest + token cron + minute data | optinet | working, port with the zero-volume/QA checks added |
| Futures router (Sleeve F) | optinet | validated; port model + risk governor; **replace index proxy with real futures feed before further trust** |
| Cross-sectional splits, embargo, calibration, traits, cost model | StockXpert | working, port as libraries |
| V3 multi-encoder | StockXpert | port as rung-3 challenger only |
| Paper ledger + halts + daily cron | optinet | working; generalize to all sleeves |
| Dashboard | optinet Next.js or Asterion React app | pick one (optinet's is more complete); read-only over `serve/` API |
| Sentiment stack (FinBERT + CSVs) | StockXpert | port into quarantine |

**Daily operational flow (IST):**

- 17:45 — EOD ingest (bhavcopy, Kite dailies, FII/DII when released, macro closes); contract checks; lake update.
- 18:30 — feature rebuild, drift monitor (feature distribution vs training reference), model inference for swing sleeve.
- 19:00 — Sleeve X recommendation set generated (ranked picks, τ-filtered, sized) → dashboard + JSON; paper ledger updated with prior-day fills; halt evaluation.
- 08:30 next day — pre-open update (SGX/GIFT gap, overnight US, news flags); recommendation confirm/veto.
- 09:15–15:30 — intraday sleeves (F, M) run on live minute stream with risk governor; no-new-entries after 14:55 (optinet rule).
- Every run appends to an immutable run manifest (config hash, model version, data snapshot hash).

---

## 7. Results

### 7.1 What exists today (the baseline the unified system inherits)

| Sleeve/source | Result | Status |
|---|---|---|
| Futures router forward-walk (optinet) | 1,128 trades, +₹429,227, Sharpe 2.17, PF 1.39, MaxDD −₹74k (Nov 2024→May 2026) | validated, but on proxy data post-Oct-2024 |
| Futures router live paper | 42 trades, +₹5,666, 42.9% win, Sharpe 0.60 (May–Jun 2026) | positive but statistically uninformative window |
| Asterion momentum slice | 97 trades, EV +44.0 bps, PF 1.99, CI-low +9.15 bps | alive, starved — Sleeve M seed |
| StockXpert blind prediction | H1 70.2% smoothed-trend accuracy, p=3.18×10⁻⁶³, ECE 0.093 | real predictive signal; not yet monetized |
| StockXpert strategy (pre-registered WF) | −64.3%, Sharpe −0.79 [CI −1.64, −0.03], 6/6 folds negative | dead as-is; the cautionary benchmark |
| Optinet V8 | Sharpe −8.22 | dead; label geometry autopsy retained as tooling |
| Recommender V2 | 61.6% backtest hit → 54.2% (2025); promoted=false | dead as-is; cross-sectional rank features salvaged |

### 7.2 What the unified system must report (standing results schema)

Every evaluation run emits one canonical report (JSON + MD) with: net return, Sharpe (+ block-bootstrap 95% CI), DSR with global config count, PF, win rate, per-trade EV bps with CI, max DD, trade count, trading-day count, symbol concentration, per-fold table, baseline comparison table, cost breakdown, and the label-viability audit for its target. **No result exists outside this schema** — this single rule prevents the +24.41%-headline-vs-+11.51%-audited discrepancy that StockXpert shipped.

---

## 8. Result Interpretation

Rules for reading any MERIDIAN result — written down now so future-us can't rationalize:

1. **CI-low is the number, not the mean.** Asterion's +44 bps slice mattered because CI-low was +9.15, not because the mean was large.
2. **Accuracy claims must name their target.** "70% accuracy" on a centered-smoothed label is a *representation-learning* result, not a trading result. Any accuracy quoted in MERIDIAN reports must state: raw/tradable vs. transformed target, horizon, coverage (% of days it fires), and sample count. High-conviction cells with < 100 samples are flagged unreliable (StockXpert's H10@0.75 = 63 samples lesson).
3. **A single good year is selection until proven otherwise.** Any headline from one period must be accompanied by the pre-registered multi-fold number. If they diverge (StockXpert: +11.51% 2025 vs −64.3% walk-forward), the walk-forward number is the truth.
4. **Compare against the boring alternatives first.** Buy-and-hold Nifty100 did +131% over StockXpert's walk-forward window. A strategy losing to random entry is not "almost there" — it is negative-alpha with extra steps.
5. **In-sample→OOS decay is expected; quantify it.** Recommender V2 decayed 61.6%→54.2%. MERIDIAN tracks a standing decay ratio per sleeve; > 30% decay in edge triggers re-quarantine review.
6. **Live-vs-backtest divergence is a first-class metric.** Paper fills are compared to backtest-simulated fills on the same signals daily; divergence beyond the slippage budget halts promotion of anything downstream.

---

## 9. Backtest

### 9.1 Engine specification

- Event-driven daily/minute simulator (extend Asterion's engine; StockXpert's `PortfolioBacktester` mechanics for stops/targets/trailing).
- **Full-cost only** (P2): brokerage, STT (per product type), stamp, exchange, GST, SEBI; slippage as a function of order size vs. ADV and spread bucket; costs table versioned.
- Fill realism: next-bar-open entries for EOD signals (no same-close fills), first-touch barrier logic intrabar resolved conservatively (stop before target when both touch in one bar), circuit-limit and liquidity halts respected.
- PIT universe join: a symbol is tradable on date t only if in the index/universe on date t.

### 9.2 Validation protocol (pre-registered, per sleeve)

1. **Label viability audit** (before any training).
2. **Cross-sectional split** (train/val/test/blind stock sets, 10-day embargo) for model development — StockXpert's proven design.
3. **Walk-forward strategy selection**: rolling 3y-select/1y-OOS folds; selection metric = worst-fold net return; 30-trade eligibility floor per config; the *entire* grid is committed to git before OOS execution.
4. **Statistics**: block-bootstrap Sharpe CI (block = 20d, n = 10k), DSR with cumulative k, binomial tests on hit rates.
5. **Sealed test**: final ~12 months untouched; spent exactly once per sleeve major-version; result recorded regardless of outcome (Asterion's sealed-test ethic: a spent-and-failed seal is a real result).

### 9.3 Sensitivity suite (mandatory before promotion)

- Cost ×1.5 and ×2 stress (router's Tier-1B pattern).
- Entry-time jitter ±1 bar; parameter-neighborhood stability (edge must not live on a knife's-edge config).
- Score-quintile monotonicity (router's Tier-1C: Q5 vs Q1 spread must be positive and ordered).
- Sub-period consistency (per-year table); regime-conditional table (bull/bear/chop via causal classifier).

## 10. Backtest Interpretation

- **Green**: all §5.6 gates pass, sensitivity suite passes → eligible for paper trading.
- **Yellow**: gates pass but sensitivity marginal (e.g., cost ×1.5 kills CI-low) → extended walk-forward, no promotion; explicitly logged as "yellow" so it can't quietly become green.
- **Red**: any gate fails → quarantine with a written autopsy (V8-style diagnosis: is it label geometry, decayed edge, concentration, or costs?). Autopsies are kept — the three repos' failure catalog is the most valuable dataset this project owns.
- Never interpret grid-search winners as edge (StockXpert's "H10 Top-1 mirage"). The unit of evidence is the *pre-registered protocol outcome*, not the best cell.
- A backtest that can't be reproduced from its manifest (config hash + data snapshot + seed) is void.

---

## 11. Paper Trading

### 11.1 Structure (port optinet's working loop, generalize)

- **Ledger**: append-only per-sleeve CSV/Parquet with signal timestamp, model version, intended price, simulated fill, costs, exit, P&L, and the backtest-expected fill for divergence tracking.
- **Cadence**: cron per §6 daily flow; failures alert (optinet's last run failed silently on EOD cache — add hard alerting).
- **Halts** (per sleeve): hard halt on cumulative DD breach (router: −₹150k pattern, set per-sleeve as % of paper capital); soft alerts on 30d Sharpe < 0.5, 5-day loss streaks, drift-monitor triggers. Kill-switch file honored by all runners.
- **Live gate**: broker execution stays behind optinet's triple-key gate (`--live` flag + env var + confirm token). v1 does not go live.

### 11.2 Graduation criteria (paper → live-eligible)

| Criterion | Threshold |
|---|---|
| Duration | ≥ 6 months paper |
| Trades | ≥ 200 (sleeve-level) |
| Per-trade EV CI-low | > 0 after full costs |
| Paper vs backtest fill divergence | within slippage budget |
| Realized calibration | ECE ≤ 0.10 vs stated probabilities |
| Ops | zero unexplained missed/duplicate signals; all halts functioned when triggered |

The router's current 42-trade positive window explicitly does **not** meet this bar — it continues paper trading under Sleeve F until it does.

### 11.3 Recommendation output (the user-facing product)

Daily JSON + dashboard card per pick: symbol, side, sleeve, entry zone, stop, target, horizon, calibrated P(win), size %, and evidence link (model version, gate report). If no pick clears τ, the system says **"no trade today"** — Recommender V2's zero-pick champion was embarrassing only because the docs claimed otherwise; silence is a valid, honest output.

---

## 12. Build Roadmap

| Phase | Weeks | Deliverable | Gate to next phase |
|---|---|---|---|
| **0 — Consolidation** | 1–3 | Monorepo skeleton; port Asterion lake/contracts/gates; port optinet Kite ingest + cron + ledger; port StockXpert splits/calibration/cost libs; data-QA dashboard | All inherited data reconciles; contract tests green |
| **1 — Data completeness** | 3–6 | PIT constituents table; real futures feed (kill index proxy); FII/DII availability stamps; sentiment stack in quarantine; label-viability audit tooling | PIT backtest of a trivial momentum strategy runs end-to-end with full costs |
| **2 — Sleeve F hardening** | 5–8 | Router re-validated on real futures data (no proxy), multi-tree retrain evaluated vs the single-tree incumbent, continued paper under new ledger | Router passes §5.6 gates on real-feed walk-forward |
| **3 — Sleeve M scaling** | 7–10 | Momentum slice re-run on Nifty 200–500 universe + full history to clear the 300-trade/concentration gates | Promoted or autopsied |
| **4 — Sleeve X research** | 9–16 | Rung 0–2 on ranking labels; pre-registered walk-forward; only then rung-3 V3 challenger (multi-seed) | Any rung passes gates → paper |
| **5 — Paper operations** | 12+ | All promoted sleeves paper-trading with dashboard, alerting, drift monitoring; monthly evidence reviews | §11.2 graduation table |
| **6 — Live gating** | 24+ | Broker adapter live behind triple-key gate, minimum capital, per-sleeve caps | Only sleeves that graduated §11.2 |

Parallelism note: Phases 2–4 are independent sleeves on a shared platform — this is where the unified architecture pays for itself.

---

## 13. Risks & Open Questions

1. **The only validated edge is fragile.** Router = effectively a single tree, trained on futures, papered on proxy. Phase 2 may kill it. Acceptable — better dead in validation than dead in production.
2. **Sleeve X may simply not clear costs at daily horizon.** StockXpert's honest walk-forward suggests large-cap swing alpha after 41 bps is thin. Mitigations: ranking (long-short spread) instead of directional bets, lower turnover policies, mid-cap liquidity band expansion. If it fails gates, that is a result, not a defeat.
3. **PIT constituents history** is the hardest new data ask; without it every equity backtest inherits survivorship inflation. Budget real effort; NSE publishes historical index changes but assembly is manual.
4. **Sentiment/news may never earn promotion.** Fine — it stays quarantined. Do not let sunk FinBERT cost force it into models.
5. **Single-developer ops load.** Cron + alerting + monthly evidence reviews must stay lightweight; the dashboard and alert bot are not optional polish, they are what makes reliability real.
6. **Discipline decay.** Every failure catalogued in §7.1 happened when a shortcut bypassed a gate (light costs, proxy data, best-cell selection, doc claims ahead of code). The gates only work if nothing ships around them — including "just this once" recommendation posts.

---

## Appendix A — Component Disposition Matrix

| Asset | From | Disposition |
|---|---|---|
| Bronze/silver/gold lake, DuckDB | Asterion | **adopt** |
| Source contracts + quarantine + snapshots | Asterion | **adopt** |
| Promotion gates + sealed tests + evidence reports | Asterion | **adopt, harden thresholds** |
| Momentum slice | Asterion | **scale (Sleeve M)** |
| RegimeGraphAlphaNet | Asterion | drop (untrained stub) |
| Futures router v0 + risk governor + halts | optinet | **adopt (Sleeve F), fix proxy skew** |
| Kite ingest + token cron + minute lake | optinet | **adopt** |
| Paper ledger + daily cron + kill switches + triple-key gate | optinet | **adopt, add alerting** |
| V8 diagnosis tooling (label economics) | optinet | **adopt as standing audit** |
| V8 ensemble, Recommender V2 runtime, options tracks | optinet | drop (autopsies retained) |
| Cross-sectional ranks (Stage B features) | optinet | **salvage into Sleeve X features** |
| Cross-sectional splits + embargo + `assert_no_leakage` | StockXpert | **adopt** |
| Stock-traits conditioning | StockXpert | **adopt** |
| Isotonic calibration + confidence execution | StockXpert | **adopt** |
| Full Indian cost model | StockXpert | **adopt, full-cost-only** |
| Pre-registered walk-forward protocol | StockXpert | **adopt as the law** |
| V3 multi-encoder | StockXpert | rung-3 challenger only, multi-seed |
| Centered-smoothed labels | StockXpert | drop for trading; research-only |
| FinBERT/GDELT sentiment stack | StockXpert | port into quarantine |
| V4 scaffold, 74 MB tarball, paper-trade skeleton | StockXpert | drop |
