# Live collectors

Every collector writes only through `ingest.collector_base`: each observation
gets immutable parsed JSONL and verbatim raw-body files under
`$COLLECTOR_LAKE_ROOT/collectors/<collector>/...`, plus an appended manifest
entry. Replaying an identical raw payload records a manifest `skipped` note;
it never changes an earlier file.

```dotenv
# Required only to place the append-only lake outside the repository.
# COLLECTOR_LAKE_ROOT=./collector_lake

# GIFT Nifty: set only when a licensed NSE IX/GIFT or vendor adapter is added.
# GIFT_NIFTY_FEED_URL=
# GIFT_NIFTY_API_KEY=

# Global minute data: yfinance is optional/best-effort recent data. A licensed
# provider adapter should use these names rather than committing credentials.
# GLOBAL_MINUTE_VENDOR_URL=
# GLOBAL_MINUTE_VENDOR_API_KEY=

# NSE announcements and option chain require no stored secret for their public
# endpoints, but both need session/header/rate-limit operations. A licensed
# alternative may use these variables.
# NSE_ANNOUNCEMENTS_API_KEY=
# NSE_OPTION_CHAIN_API_KEY=

# Authoritative ADR/ETF closes/actions and FII/DII releases are vendor/feed
# decisions; yfinance only supplies the former best-effort fallback.
# ADR_ETF_VENDOR_API_KEY=
# FII_DII_FEED_URL=
# FII_DII_API_KEY=
```

Fetch status:

- `gift_nifty`: stub; needs a licensed NSE IX/GIFT 1-minute feed or vendor.
- `global_minute`: real best-effort recent yfinance fetch for ES, NQ, Nikkei,
  KOSPI, HSI, USDINR, and Brent where Yahoo supports each ticker; a licensed
  feed is needed for complete exchange-grade minutes.
- `snapshot_ledger`: real local-lake implementation; no feed credential.
- `nse_announcements`: reuses the existing live NSE announcement fetch/parser;
  source dissemination time is retained and corrections become new payloads.
- `option_chain`: stub; needs operated NSE public option-chain sessions with
  conservative rate limiting, or a licensed options feed.
- `adr_etf_closes`: real best-effort daily yfinance close/action fetch for
  INFY, HDB, IBN, WIT, INDA, and EPI; a licensed US close/action feed unblocks
  authoritative coverage.
- `fii_dii`: stub that reuses the existing release-time parser; needs a
  documented NSE/NSDL daily release artifact/feed.

Ops schedules the individual collectors at their desired cadence. The snapshot
ledger must run at all four clocks (IST):

```cron
CRON_TZ=Asia/Kolkata
0 8 * * 1-5 /path/to/meridian/.venv/bin/python /path/to/meridian/scripts/collectors/snapshot_ledger.py
0 9 * * 1-5 /path/to/meridian/.venv/bin/python /path/to/meridian/scripts/collectors/snapshot_ledger.py
15 9 * * 1-5 /path/to/meridian/.venv/bin/python /path/to/meridian/scripts/collectors/snapshot_ledger.py
45 9 * * 1-5 /path/to/meridian/.venv/bin/python /path/to/meridian/scripts/collectors/snapshot_ledger.py
```
