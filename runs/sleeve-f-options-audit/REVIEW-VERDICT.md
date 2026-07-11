# Options archive audit — reviewer verdict (Fable, 2026-07-12)

Scan basis: `audit_report.md` (30 stratified sample days, read-only).

**Verdict: NOT READY for the C3 cascade as-is.** Against the four go-criteria:

1. **True intraday pre-decision observations — FAIL for the continuation.** The legacy CSV archive (2020→) is genuinely 1-minute intraday, but the parquet continuation is date/EOD grain. An intraday option feature family cannot be served point-in-time from an EOD store. Timezone/provenance of CSV timestamps also unestablished.
2. **≥95% valid-surface minutes — provisionally met on legacy CSV samples (99.7–100%)** under the audit's proxy (≥5 strikes each side, both legs, nonzero LTP), but unassessable for the sparse later period. Sample-basis only.
3. **CSV↔parquet seam reconciles — NOT MET.** Sampled contract joins show substantial close drift; later samples produce no joins at all. This is a grain change, not a format conversion.
4. **Live continuation — NOT MET.** Latest data 2026-07-10, nothing demonstrably appending; the W-8 `option_chain` collector (stub pending NSE chain feed) is the intended fix.

**Consequences:**
- The options step of the Six-Week Gate Cascade cannot pass on the current archive. If the cascade reaches options before an intraday-parity source exists, the cascade rule says: fail the audit, move to the next family. No k is burned.
- Starting the option-chain live collector now (W-8) is the only action that changes this picture by C3 time; 60 live sessions of snapshots are required by the cascade anyway.
- Deeper audit (full-archive identifier parse, timezone proof, exchange-calendar-based coverage) only worth commissioning once an intraday continuation source is identified.

No PnL, labels, or outcome data were consulted; this verdict is provenance/coverage-only and burns no k.
