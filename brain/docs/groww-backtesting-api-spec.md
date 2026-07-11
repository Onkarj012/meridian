# Groww Trade API — Backtesting Endpoints: Implementation Spec

**As of:** 2026-07-11. **Purpose:** machine-precise reference for building a Groww ingest of expired NSE NIFTY futures 1-min OHLCV+OI. Extracted verbatim from official docs this session:
- https://groww.in/trade-api/docs/curl (Introduction — auth, checksum, rate limits)
- https://groww.in/trade-api/docs/curl/backtesting (the three Backtesting endpoints)
- https://groww.in/trade-api/docs/curl/historical-data (legacy endpoint — deprecated, NOT used here)

Everything below is from those pages unless flagged. Anything the docs do not state is in §5 "Unresolved — verify live". Companion research doc: `brain/docs/expired-futures-data-sources.md`.

---

## 1. Authentication

### 1.1 Key generation
- API keys are created at: `https://groww.in/trade-api/api-keys` (requires Groww account + Trading API subscription, ₹499/mo + taxes early-bird per https://groww.in/trade-api).
- Two key types exist: **"approval"** (API key + secret) and **"totp"** (API key + TOTP secret).

### 1.2 Token endpoint (both modes)

```
POST https://api.groww.in/v1/token/api/access
Authorization: Bearer <USER_API_KEY>
Content-Type: application/json
```

**Mode A — approval (key + secret + checksum):**

```json
{
  "key_type": "approval",
  "checksum": "<Checksum>",
  "timestamp": "1719830400"
}
```

- `timestamp`: epoch **seconds** (10 digits). Docs: "Valid for 10 minutes. Provide the same value in request."
- `checksum` (docs, verbatim): "Checksum should be a SHA256 hash of api secret and and latest timestamp in epoch second concatenated together." Concatenation order `secret + timestamp`, hex output. Official Python example:

```python
input_str = secret + timestamp
sha256 = hashlib.sha256()
sha256.update(input_str.encode('utf-8'))
return sha256.hexdigest()
```

**Mode B — totp (key + 6-digit TOTP code):**

```json
{
  "key_type": "totp",
  "totp": "123456"
}
```

**Token response (both modes):**

```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "tokenRefId": "ref-123",
  "sessionName": "my-session",
  "expiry": "2024-07-01T12:34:56",
  "isActive": true
}
```

### 1.3 Token lifetime
- Docs: access token **"Expires daily at 6:00 AM"** (also echoed in the `expiry` response field). Mint once per day after 06:00, cache, reuse.

### 1.4 Headers on every subsequent request

| Header | Value |
|---|---|
| `Authorization` | `Bearer {ACCESS_TOKEN}` |
| `Accept` | `application/json` |
| `X-API-VERSION` | `1.0` |

### 1.5 Env-var summary for the ingest

```
GROWW_API_KEY=...          # always required
GROWW_API_SECRET=...       # approval mode (preferred for unattended ingest)
# or
GROWW_TOTP_SECRET=...      # totp mode (generate 6-digit code at call time)
```

Flow: `POST /v1/token/api/access` → cache `token` → refresh after next 06:00 (IST presumed, see §5).

---

## 2. Backtesting endpoints

Discovery chain for an expired NIFTY future: **Get Expiries → Get Contracts → Get Historical Candle Data.**
Docs: "Currently, Backtesting APIs only support CASH and FNO segments." FNO includes index futures; futures groww_symbol examples given in docs: `NSE-NIFTY-30Sep25-FUT`, `BSE-SENSEX-25Sep25-FUT`.

### 2.1 Get Expiries

```
GET https://api.groww.in/v1/historical/expiries
```

| Param | Type | Req | Format / values |
|---|---|---|---|
| `exchange` | string | yes | `NSE` or `BSE` |
| `underlying_symbol` | string | yes | e.g. `NIFTY`, `BANKNIFTY`, `RELIANCE` |
| `year` | int | no | 2020–current year; defaults to current year |
| `month` | int | no | 1–12; omit for entire year |

```bash
curl -X GET 'https://api.groww.in/v1/historical/expiries?exchange=NSE&underlying_symbol=NIFTY&year=2024&month=1' \
  -H 'Accept: application/json' \
  -H 'Authorization: Bearer {ACCESS_TOKEN}' \
  -H 'X-API-VERSION: 1.0'
```

Response (verbatim from docs):

```json
{
  "status": "SUCCESS",
  "payload": {
    "expiries": ["2024-01-25", "2024-01-31", "2024-02-29", "2024-03-28"]
  }
}
```

### 2.2 Get Contracts

```
GET https://api.groww.in/v1/historical/contracts
```

| Param | Type | Req | Format / values |
|---|---|---|---|
| `exchange` | string | yes | `NSE` or `BSE` |
| `underlying_symbol` | string | yes | 1–20 chars, e.g. `NIFTY` |
| `expiry_date` | string | yes | `YYYY-MM-DD` (a past date is the designed use) |

```bash
curl -X GET 'https://api.groww.in/v1/historical/contracts?exchange=NSE&underlying_symbol=NIFTY&expiry_date=2025-01-25' \
  -H 'Accept: application/json' \
  -H 'Authorization: Bearer {ACCESS_TOKEN}' \
  -H 'X-API-VERSION: 1.0'
```

Response (verbatim from docs — note the doc example shows only options; the futures entry should be the `...-FUT` symbol, see §5):

```json
{
  "status": "SUCCESS",
  "payload": {
    "contracts": [
      "NSE-NIFTY-02Jan25-28500-PE",
      "NSE-NIFTY-02Jan25-24000-PE",
      "NSE-NIFTY-02Jan25-26800-PE",
      "NSE-NIFTY-02Jan25-27450-PE",
      "NSE-NIFTY-02Jan25-19050-PE",
      "NSE-NIFTY-02Jan25-22300-PE",
      "NSE-NIFTY-02Jan25-28150-CE"
    ]
  }
}
```

Ingest filter: select entries ending `-FUT`.

### 2.3 Get Historical Candle Data

```
GET https://api.groww.in/v1/historical/candles
```

| Param | Type | Req | Format / values |
|---|---|---|---|
| `exchange` | string | yes | `NSE` or `BSE` |
| `segment` | string | yes | `CASH` or `FNO` (use `FNO` for futures) |
| `groww_symbol` | string | yes | e.g. `NSE-NIFTY-30Sep25-FUT` (from Get Contracts) |
| `start_time` | string | yes | `yyyy-MM-dd HH:mm:ss` or epoch seconds |
| `end_time` | string | yes | `yyyy-MM-dd HH:mm:ss` or epoch seconds |
| `candle_interval` | string | yes | one of: `1minute`, `2minute`, `3minute`, `5minute`, `10minute`, `15minute`, `30minute`, `1hour`, `4hour`, `1day`, `1week`, `1month` |

```bash
curl -X GET 'https://api.groww.in/v1/historical/candles?exchange=NSE&segment=FNO&groww_symbol=NSE-NIFTY-30Sep25-FUT&start_time=2025-09-01 09:15:00&end_time=2025-09-24 15:30:00&candle_interval=1minute' \
  -H 'Accept: application/json' \
  -H 'Authorization: Bearer {ACCESS_TOKEN}' \
  -H 'X-API-VERSION: 1.0'
```

(The doc's own curl example is a CASH/`NSE-WIPRO`/`5minute` request of identical shape; note `start_time`/`end_time` contain a space and must be URL-encoded in practice.)

Response (verbatim doc example — CASH instrument, hence `null` OI):

```json
{
  "status": "SUCCESS",
  "payload": {
    "candles": [
      ["2025-09-24T10:30:00", 245.95, 246.15, 245.05, 245.6, 735060, null]
    ],
    "closing_price": 244.6,
    "start_time": "2025-09-24 10:30:00",
    "end_time": "2025-09-24 13:30:00",
    "interval_in_minutes": 30
  }
}
```

**Candle array element order (confirmed from docs):**

| Index | Field |
|---|---|
| 0 | timestamp, `yyyy-MM-ddTHH:mm:ss` |
| 1 | open |
| 2 | high |
| 3 | low |
| 4 | close |
| 5 | volume |
| 6 | **open interest** (populated for FNO; `null` for non-FNO) |

**Data floor (verbatim):** "Data of FNO instruments are available from 2020" (elsewhere on the page: "Data of Equities, Indices and FNO instruments are available from 2020"). Covers the Nov 2024 → Mar 2026 ingest window entirely.

---

## 3. Limits

### 3.1 Max date span per candle request (from the backtesting doc)

| `candle_interval` | Max span per request |
|---|---|
| `1minute`, `2minute`, `3minute`, `5minute` | 30 days |
| `10minute`, `15minute`, `30minute` | 90 days |
| `1hour`, `4hour`, `1day`, `1week`, `1month` | 180 days |

Ingest consequence: at `1minute`, one calendar month per request per contract is safe. A NIFTY monthly future's liquid life (~3 months) = ~3 requests per contract; the full Nov 2024 → Mar 2026 window across ~17 contracts is on the order of 50–70 candle requests plus discovery calls.

### 3.2 Rate limits (from the Introduction page, verbatim table)

| Type | Requests | Per second | Per minute |
|---|---|---|---|
| Orders | Create, Modify, Cancel Order | 10 | 250 |
| Live Data | Market Quote, LTP, OHLC | 10 | 300 |
| Non Trading | Order Status, Order list, Trade list, Positions, Holdings, Margin | 20 | 500 |

Limits are shared across all APIs within a type. **The Historical/Backtesting endpoints do not appear in this table** — their bucket is undocumented (see §5). Ingest should self-throttle conservatively (e.g. ≤5 req/s) and back off on HTTP 429.

### 3.3 Pagination
- None documented for any of the three endpoints. A candles response returns the full span requested (within the §3.1 caps).

---

## 4. Ingest pseudocode

```
token = auth()                                   # §1, cache until next 06:00
for (year, month) in window(2024-11 .. 2026-03):
    expiries = GET /v1/historical/expiries?exchange=NSE&underlying_symbol=NIFTY&year=Y&month=M
    for expiry in expiries:
        contracts = GET /v1/historical/contracts?...&expiry_date=expiry
        fut = [c for c in contracts if c.endswith("-FUT")]
        for chunk in 30day_chunks(contract_life(fut)):     # life ≈ expiry-3months .. expiry
            candles = GET /v1/historical/candles?exchange=NSE&segment=FNO&groww_symbol=fut
                          &start_time=chunk.start&end_time=chunk.end&candle_interval=1minute
            store(rows: ts, o, h, l, c, vol, oi)           # oi = element [6]
```

Note: NIFTY monthly expiries are also returned interleaved with weekly *options* expiries by Get Expiries; futures exist only for monthly expiries, so non-monthly expiry dates will simply yield no `-FUT` contract.

---

## 5. Unresolved — verify live

1. **Rate-limit bucket for Historical/Backtesting endpoints** — absent from the official rate-limit table (Orders / Live Data / Non Trading only). Unknown per-second/per-minute cap; probe and honor 429s.
2. **Error response shape / error codes for these endpoints** — the backtesting page documents none (no error table, no example). General error semantics for the API were not found on the pages read. Capture real error bodies during the first run.
3. **Timezone** — `start_time`/`end_time` and candle timestamps are documented only as `yyyy-MM-dd HH:mm:ss` with no timezone stated. IST is the obvious presumption for NSE, but unconfirmed.
4. **Get Contracts futures entries** — the doc's response example lists only options symbols. That futures appear in the same `contracts` list as `...-FUT` (matching the documented futures groww_symbol format `NSE-NIFTY-30Sep25-FUT`) is inferred, not shown. If absent, construct the symbol directly as `NSE-NIFTY-{ddMMMyy of expiry}-FUT`.
5. **Whether an expired contract actually returns non-empty 1-min bars with populated OI** — the doc's candle example is a CASH instrument (OI `null`). The from-2020 floor plus the expiries/contracts flow strongly imply it, but no expired-FNO response example exists in the docs and no third-party confirmation was found (product launched 2025-10-03). This is the first live test to run.
6. **Backtesting APIs' inclusion in the ₹499/mo subscription** — the pricing page's module list still says "Historical Data (up to 3 months)", wording that predates the Oct-2025 Backtesting launch; no separate price is published anywhere read. Inclusion is the reasonable inference.
7. **"approval" mode operational semantics** — whether approval-type keys require a manual daily approval step on the Groww web console (community/search snippets suggest some daily-approval mechanism exists for one of the modes) vs. fully unattended minting with key+secret+checksum. If unattended operation matters, verify before choosing the mode; TOTP mode is fully scriptable regardless.
8. **`start_time`/`end_time` epoch-seconds variant** — documented as accepted, but all doc examples use the string form; confirm epoch input works if you prefer it.

## 6. Data quirks found in live backfill

These findings come from the offline validation of 347 locally backfilled gap
dates (no additional API calls were made during validation):

- Groww commonly emits a `15:30:00` row. On many 2025–2026 files that row has
  `oi=0`, while the preceding nonzero OI observation is usually at `15:28:00`
  or `15:29:00`. Validation therefore records an explicit `oi_from_XXXX_bar`
  note and compares OI to the last nonzero-OI row. The offline scan found a
  hard unit boundary at **2025-01-01**: the 2024-12-31 ratio
  (`bhavcopy front_oi / file last nonzero OI`) is 0.985031, while the
  2025-01-01 ratio is 99.413056. Groww's 2025+ raw OI is normalized ×100 for
  the 307 explicit backfill days. The live ingest uses the calendar ratio per
  fetch and skips scaling when the raw ratio is already near one, so a later
  Groww API fix is safe.
- The incumbent archive vintage is mixed: many 2023 and 2024 files include
  `15:30:00`, while others end at `15:29:00`. The frozen Sleeve F feature cache
  has 315 post-warmup rows/day ending at `15:29:00` from a 375-minute
  `09:15`–`15:29` input session. New files retain the archive's `15:30` row;
  close validation uses the volume-weighted mean close of bars from 15:00:00
  onward (NSE's last-30-minute VWAP); the final trade close remains in the
  report as diagnostic information.
- `2024-11-01` (Diwali Muhurat) has no regular-session file and
  `2025-10-21` (if present) is an evening-only 61-bar Muhurat file. Both are
  explicit skip-with-note dates, not validation failures.
- NSE reuses instrument ID `53001` across two September-2025 expiry mappings:
  `2025-09-25` in the early July rows and `2025-09-30` from August onward. The
  target `2025-09-29` and `2025-09-30` rows map unambiguously to `2025-09-30`;
  their file volumes equal 75 times the bhavcopy contract volumes.
- The `2025-01-30` contract retained lot size 25, so January 2025 file volume
  is exactly 25 times bhavcopy contracts. Later 2025 contracts use lot 75 and
  2026 contracts use lot 65. Raw ratios near 64.1 or 74.6 are scaled-lot
  noise within the existing ±5% tolerance; `2025-09-26` is the exception, a
  truncated 346-bar file ending at 15:00 with only 0.819 of expected volume.
  Validation records this as `truncated_day` and fails it as
  `session_truncated` so the later live retry can replace it.
- After normalization, residual Groww OI tails around expiry are reported as
  `oi_timing_drift` notes when their bhavcopy/file ratio remains within the
  measured 0.2–2.0 envelope. The shared validator's strict 5% OI default is
  unchanged for other providers.
