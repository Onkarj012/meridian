# NSE NIFTY Index Futures — Intraday Round-Trip Cost Schedule (2020-01-01 → 2026-07-11)

**Scope:** Date-versioned cost schedule for trading one lot of NSE NIFTY 50 index futures intraday (buy + sell same day, "round trip"), covering every rate/date-of-change found for STT, NSE transaction charges, SEBI turnover fee, stamp duty, GST, and discount-broker brokerage, within the window 2020-01-01 to 2026-07-11. Also documents the Upstox Developer API "Plus" plan.

**As of:** 2026-07-11 (today). All "current" rates below are as understood on this date. Where a rate changes on a future-dated but already-legislated date (e.g. 2026-04-01), that is noted explicitly.

**Research method:** Primary sources only where locatable — NSE circulars (`nseindia.com` / `nsearchives.nseindia.com` / `archives.nseindia.com`), SEBI circulars (`sebi.gov.in`), Finance Act / Finance Bill / Budget Speech text (`indiabudget.gov.in`, `egazette.gov.in`, `pib.gov.in`), and official broker pricing pages (`zerodha.com/charges`, `upstox.com/brokerage-charges`, `upstox.com/plus`). Secondary sources (broker blogs, news, tax-explainer sites) are used only to corroborate a primary source or to point at one; anywhere a primary source could not be directly read or located, this is flagged explicitly rather than presented as confirmed. Several NSE circular PDFs were located (exact circular number + date, corroborated by independent secondary citations quoting them) but their raw text could not be extracted by the fetch tooling used in this session (PDF encoding/compression made them non-machine-readable, and several requests timed out). Those cases are called out per-row and summarized again in the "Could not verify" section.

---

## 1. STT (Securities Transaction Tax) — futures, sell side only

Futures STT is charged **only on the sell leg**, on the traded (notional) value.

| effective_from | rate (sell side) | source |
|---|---|---|
| (in force entering the window, until 2023-03-31) | 0.01% | Consistently cited as the "current" pre-2023 rate by multiple sources reporting the 2023 change (e.g. secondary corroboration below). **Primary Finance Act text establishing 0.01% was not independently re-verified in this pass** — it predates the research window (believed to originate from the Finance Act 2013 reduction from 0.017%, but this specific earlier change was not re-confirmed here). See "Could not verify." |
| 2023-04-01 | 0.0125% (25% increase) | Finance Act 2023 (No. 8 of 2023), assented 2023-03-31 (gazette: https://egazette.gov.in/WriteReadData/2023/244174.pdf — located but text not machine-extractable in this session). Implementing NSE circular: **NSE/FATAX/56235**, dated 2023-04-01: https://archives.nseindia.com/content/circulars/FATAX56235.pdf (URL located; PDF text not machine-extractable — content corroborated via https://www.taxmanagementindia.com and multiple broker STT explainers quoting "0.0125% from the current 0.01%, effective 1 April 2023"). |
| 2024-10-01 | 0.02% | Finance (No. 2) Act 2024 (notified in the Gazette of India 2024-08-16), Section 162, amending Section 98(c) of the Finance (No. 2) Act, 2004. Gazette: https://egazette.gov.in/WriteReadData/2024/256436.pdf (located; not machine-extractable). Implementing NSE circular: **NSE/FATAX/63809**, dated 2024-09-09: https://nsearchives.nseindia.com/content/circulars/FATAX63809.pdf (URL located; PDF text not machine-extractable — content corroborated by Zerodha's own official notice: https://zerodha.com/z-connect/business-updates/revision-in-exchange-transaction-charges-and-securities-transaction-tax-from-october-1-2024, and by https://taxguru.in/income-tax/finance-no-2-act-2024-key-amendments-relating-direct-taxes.html quoting the exact amended clause "0.0125 per cent to 0.02 per cent"). |
| 2026-04-01 | 0.05% | Union Budget 2026-27, Finance Minister's speech, proposing STT on futures raised to 0.05% from 0.02%. Primary source: Budget Speech, https://www.indiabudget.gov.in/doc/budget_speech.pdf (located; PDF text not machine-extractable by the fetch tool in this session — a search-engine snippet directly quoting the speech text was used as corroboration: "I propose to raise the STT on futures to 0.05% from the existing 0.02%"). Implementing NSE circular: **NSE/FATAX/73524**, dated 2026-03-31: https://nsearchives.nseindia.com/content/circulars/FATAX73524.pdf (URL located via search; fetch timed out repeatedly — not read in this session, flagged below). |

**Note:** this last change (2026-04-01) falls inside the requested window (ends 2026-07-11), so it is included and is the current rate as of today.

---

## 2. NSE transaction charges — equity index futures

| effective_from | rate | source |
|---|---|---|
| (through 2024-09-30) | Slab-based, ranging ~₹1.73–₹1.88 per lakh of traded value per side, depending on the trading member's monthly turnover slab (higher volume → lower effective rate; the difference was a rebate paid back to brokers) | Reported consistently across secondary sources (e.g. Business Standard: https://www.business-standard.com/markets/stock-market-news/nse-and-bse-revise-their-transaction-fees-to-comply-with-sebi-circular-124092701207_1.html; BusinessToday: https://www.businesstoday.in/markets/company-stock/story/transaction-charges-on-nse-bse-mcx-higher-stt-key-changes-for-investors-from-today-448262-2024-10-01). **The primary NSE slab-rate circular itself was not located/read directly in this session** — flagged in "Could not verify." |
| 2024-10-01 | Flat/uniform **₹1.73 per lakh (0.00173%) per side**, both buy and sell, slabs and volume-based rebates abolished | SEBI circular **SEBI/HO/MRD/TPD-1/P/CIR/2024/92**, "Charges levied by Market Infrastructure Institutions – True to Label," dated 2024-07-01: https://www.sebi.gov.in/legal/circulars/jul-2024/charges-levied-by-market-infrastructure-institutions-true-to-label_84506.html (fetched directly, circular number and date confirmed on the SEBI page itself). Implementing NSE circular referenced by trading members as **NSE/FATAX/63809** (Currency Derivatives segment) and a companion equity-derivatives revision circular, e.g. **NSE/FA/64232**: https://nsearchives.nseindia.com/content/circulars/FA64232.pdf (URL located; PDF text not machine-extractable in this session — corroborated by Zerodha's official notice and by search-engine snippets quoting "Rs 1.73 per lakh of traded value" as the new uniform equity-futures rate effective 2024-10-01). |
| No further revision found, 2024-10-01 → 2026-07-11 | Same, ₹1.73/lakh (0.00173%) | No NSE circular revising the equity index futures transaction charge again after 2024-10-01 was found in this research pass. This is a **negative finding, not exhaustively confirmed** — see "Could not verify." |

---

## 3. SEBI turnover fee (regulatory fee)

| effective_from | rate | source |
|---|---|---|
| Entering window (2020-01-01) through 2026-07-11, no change found | ₹10 per crore of turnover (0.0001%) for all securities other than debt instruments (includes equity derivatives / index futures) | SEBI (Regulatory Fee on Stock Exchanges) Regulations, 2006: https://www.sebi.gov.in/legal/regulations/apr-2017/sebi-regulatory-fee-on-stock-exchanges-regulations-2006-last-amended-on-march-6-2017-_34704.html (fetched; page metadata shows "last amended on March 6, 2017"). Rate corroborated on NSE's own member-facing page: https://www.nseindia.com/regulations/member-compliance-sebi-turnover-fees, and consistently across every broker pricing page checked (Zerodha, Upstox). |

Secondary sources also reference a further amendment, SEBI notification **SEBI/L.A.D.-N.R.O./G.N./2019/03** dated 2019-03-22, effective 2019-04-03 — this predates the window and does not appear to have changed the ₹10/crore rate itself (it is described as a "revised" restatement). Not independently confirmed from the notification text in this session — see "Could not verify."

GST note: 18% GST is applied on top of this fee (see Section 5).

---

## 4. Stamp duty — futures, buy side only

| effective_from | rate | source |
|---|---|---|
| 2020-07-01 | 0.002% (₹200 per crore) of traded value, buy side only | Legal basis: Finance Act, 2019 amendments to the Indian Stamp Act, 1899, and the Indian Stamp (Collection of Stamp-Duty through Stock Exchanges, Clearing Corporations and Depositories) Rules, 2019, notified by the Department of Revenue on 2019-12-10 (G.S.R. 901(E)), which came into force 2020-01-09, with the centralised collection mechanism itself made operative from 2020-07-01 (delayed from an earlier date due to COVID-19). Government press release: https://www.pib.gov.in/PressReleasePage.aspx?PRID=1635399 (URL located via search; direct fetch returned HTTP 403 in this session — not independently read, see below). Corroborating primary-adjacent source: PwC tax alert citing the same rules and date: https://www.pwc.in/assets/pdfs/news-alert-tax/2020/pwc_news_alert_3_july_2020_stamp_duty_on_securities_transactions_effective_from_1_july_2020.pdf (URL located; fetch returned HTTP 403 in this session, not independently read). Implementing NSE circular: **NSE/CMPT/43079**, dated 2020-01-01 (referenced via search results, not independently read). Broker corroboration: https://zerodha.com/z-connect/general/uniform-stamp-duty and https://zerodha.com/marketintel/bulletin/259741/uniform-stamp-duty-applicable-from-july-1-2020. |
| No change found, 2020-07-01 → 2026-07-11 | Same, 0.002% buy side | No subsequent revision to the futures stamp-duty rate was found in this research pass. |

---

## 5. GST

| effective_from | rate | applies to | source |
|---|---|---|---|
| No change found within window | 18% | Brokerage + SEBI turnover fee + exchange transaction charges (the *service* components). **Not** applied to STT or stamp duty (these are taxes/statutory levies, not a taxable "service"). | HSN/SAC code 997152 ("Brokerage and related securities and commodities services") carries an 18% GST rate under the CBIC/GST Council rate schedule: https://cbic-gst.gov.in/gst-goods-services-rates.html (general schedule page, generic reference — the specific 18% rate for this SAC code was corroborated via secondary explainer https://www.indiafilings.com/learn/gst-on-stock-broking-services/ and via both Zerodha's and Upstox's own official charges pages, which independently state "GST: 18% on (brokerage + SEBI charges + transaction charges)"). This rate has applied since GST's introduction in July 2017, predating the research window; no in-window rate change was found. |

---

## 6. Brokerage — discount brokers, futures, per executed order

| effective_from | broker | rate | source |
|---|---|---|---|
| Current (fetched 2026-07-11); no in-window change confirmed | Zerodha | ₹20 flat per executed order, or 0.03% of order value, whichever is **lower** | https://zerodha.com/charges/ (fetched directly). Multiple current secondary sources state this flat-fee structure "has not changed since Zerodha launched" its discount model — **the exact date this specific ₹20 figure first took effect, and confirmation it was never revised at any point 2020–2026, was not independently verified against a dated historical announcement** in this session. See "Could not verify." |
| Current (fetched 2026-07-11); no in-window change confirmed | Upstox | ₹20 flat per executed order, or 0.05% of order value, whichever is **lower**, on the **Basic** plan; **₹30** flat per order for users on the **Plus** plan (Plus trades users a higher flat brokerage in exchange for feature access — see Section 7) | https://upstox.com/brokerage-charges/ (fetched directly) and https://upstox.com/help-center/does-the-brokerage-plan-change-with-upstox-plus-264072/ (fetched directly: "the flat fee per order increases from ₹20 to ₹30, but the percentage-based caps remain identical across both plans"). No historical change to the ₹20 Basic-plan figure was found or claimed on the page; not independently dated. |

**Zero-brokerage schemes:** both brokers offer ₹0 brokerage on **equity delivery** trades only. Neither broker was found to offer a zero-brokerage scheme for **futures** at any point in the window — futures brokerage is always the flat-fee-or-percentage structure above.

---

## 7. Worked example — total round-trip cost in bps of notional, one NIFTY futures lot

**Assumptions used** (stated explicitly, since exact historical NIFTY closing levels could not be pulled from a verified primary historical-data source in this session — see "Could not verify"):

- Broker: Zerodha-style flat ₹20/executed order, or 0.03% whichever lower (0.03% never binds at these notional sizes, so ₹20 is used for every leg).
- NIFTY lot size at each date is taken from the NSE lot-size revision history assembled during this research (dates corroborated via multiple broker/market-intel bulletins, e.g. https://zerodha.com/marketintel/bulletin/291849/revision-in-lot-size-of-nifty-and-fo-contracts and https://zerodha.com/marketintel/bulletin/429705/revision-in-lot-size-of-index-derivative-contracts-from-december-30-2025; the underlying NSE circulars were not directly read in this session — flagged below):
  - 2021-07 expiry onward: lot size **50** (down from 75)
  - New contracts from 2024-04-26 onward: lot size **25** (halved from 50)
  - Transition completed by 2024-12-26: lot size **75** (raised per SEBI's Rs 15–20 lakh minimum-contract-value directive)
  - Transition completed by 2025-12-31 (initiated 2025-10-28): lot size **65**
  - So: 2023-06-01 → lot 50; 2024-11-01 → lot 25 (pre the Dec-2024 transition to 75); 2025-06-01 → lot 75.
- NIFTY index level at each date is an **illustrative approximation**, not a verified NSE historical closing print: ~18,600 (2023-06-01), ~24,300 (2024-11-01), ~24,750 (2025-06-01).
- Applicable rates per Sections 1–5 above at each date.

| Date | Lot size | Assumed index | Notional (₹) |
|---|---|---|---|
| 2023-06-01 | 50 | 18,600 | 9,30,000 |
| 2024-11-01 | 25 | 24,300 | 6,07,500 |
| 2025-06-01 | 75 | 24,750 | 18,56,250 |

### 2023-06-01 (STT 0.0125%, NSE txn 0.00188% assumed pre-reform slab, SEBI ₹10/cr, stamp 0.002%, GST 18%)

Buy leg: brokerage ₹20.00 + NSE txn ₹17.48 + SEBI ₹0.93 + stamp ₹18.60 + GST(18% × (20+17.48+0.93)=₹6.91) = **₹63.93**
Sell leg: brokerage ₹20.00 + NSE txn ₹17.48 + SEBI ₹0.93 + STT ₹116.25 + GST(18% × (20+17.48+0.93)=₹6.91) = **₹161.58**
Round-trip total ≈ **₹225.51** → **2.42 bps** of notional

### 2024-11-01 (STT 0.02%, NSE txn 0.00173% post-reform flat, SEBI ₹10/cr, stamp 0.002%, GST 18%)

Buy leg: brokerage ₹20.00 + NSE txn ₹10.51 + SEBI ₹0.61 + stamp ₹12.15 + GST(18% × (20+10.51+0.61)=₹5.60) = **₹48.87**
Sell leg: brokerage ₹20.00 + NSE txn ₹10.51 + SEBI ₹0.61 + STT ₹121.50 + GST(18% × (20+10.51+0.61)=₹5.60) = **₹158.22**
Round-trip total ≈ **₹207.09** → **3.41 bps** of notional

### 2025-06-01 (STT still 0.02% — pre the 2026-04-01 change, NSE txn 0.00173%, SEBI ₹10/cr, stamp 0.002%, GST 18%)

Buy leg: brokerage ₹20.00 + NSE txn ₹32.11 + SEBI ₹1.86 + stamp ₹37.13 + GST(18% × (20+32.11+1.86)=₹9.71) = **₹100.81**
Sell leg: brokerage ₹20.00 + NSE txn ₹32.11 + SEBI ₹1.86 + STT ₹371.25 + GST(18% × (20+32.11+1.86)=₹9.71) = **₹434.93**
Round-trip total ≈ **₹535.74** → **2.89 bps** of notional

**Reading the trend:** the pure percentage-based costs (STT + NSE txn + SEBI fee + stamp + their GST) rise from ~1.92 bps (2023-06) to a flat ~2.63 bps (2024-11 and 2025-06, where NSE-txn/STT/stamp/SEBI rates are identical — the Oct-2024 txn-charge cut roughly offsets the Oct-2024 STT hike). What moves the *total* bps between 2024-11 and 2025-06 is the flat ₹20/leg brokerage: at the smaller 2024-11 notional (₹6.08L) the flat fee is a bigger share of notional (~0.66 bps) than at the larger 2025-06 notional (₹18.56L, ~0.22 bps), pulling total bps down as lot size (and thus notional) grows even though the percentage rates are unchanged.

---

## 8. Upstox Developer API — "Plus" plan

### Price / subscription cost

Primary evidence indicates **Upstox Plus is currently free to activate — there is no separate subscription fee**. The trade-off is not a subscription price but a **higher flat brokerage** (₹30/executed order on Plus vs ₹20/executed order on Basic — see Section 6), plus Upstox's own Plus-plan terms document states that if the plan becomes chargeable in future, users will be notified in advance (implying it is not chargeable today). This is a **correction to the task's premise** that Plus has a discrete subscription price; no such price could be found because none currently exists.
Source: https://upstox.com/help-center/does-the-brokerage-plan-change-with-upstox-plus-264072/ (fetched directly); https://upstox.com/plus/ (fetched directly); community moderator reply in https://community.upstox.com/t/upstox-plus-pricing-plan/8988 ("Upstox Plus is available for FREE to subscribe. However, the brokerage charges will be ₹30 instead of ₹20") — this last one is a community-forum reply from an Upstox staff/moderator account, treated here as broker-official but not a formal pricing-page citation; flagged as weaker than a pricing page.

### What it includes vs Basic/free tier

- Trading-side: seconds-level charting (1/5/15-second candles), 20 levels of demand/supply zones (vs 5 on Basic), one-tap chart execution, TBT (tick-by-tick) "likely to trade" price insight, discounted Strategy Builder orders (₹30 for 2-leg / ₹60 for 4-leg, ~25% cheaper), lower Margin Pledge interest (16% vs 18% annually), zero charges on netbanking transfers/instant withdrawals, cheaper auto-square-off (₹50 vs ₹75), dedicated exchange order-execution line, priority support, free UpLearn courses (worth ₹1,500).
- API-side: **5 websocket connections** (vs 2 on Basic), **OHLC/historical data for expired contracts** (the Expired Instruments API family), and market-depth (30 levels) for up to 50 symbols.
Source: https://upstox.com/plus/ (fetched directly).

### How to subscribe

Upgrade via the "Get Plus" option in the top-right corner of the Upstox mobile app or on pro.upstox.com, then activate from the benefits screen ("activate Plus instantly").
Source: https://upstox.com/plus/ (fetched directly).

### Is the base (non-Plus) API free?

Yes — standard order-placement, historical, and live-market-data APIs are described as free of cost on Upstox's developer community; what specifically requires Plus is the **Expired Instruments (historical candle data for expired F&O contracts) API**, the extra websocket connections, and deeper market depth.
Source: https://community.upstox.com/t/developer-api-pricing/3796 and https://community.upstox.com/t/cost-of-api-usage/9993 (community forum, Upstox's own developer support channel — treated as broker-official but not a formal pricing-page citation). The Expired Instruments API documentation itself states requirement of a Plus subscription via error code `UDAPI1149`: "This API is available exclusively with an Upstox Plus plan subscription" — fetched directly from https://upstox.com/developer/api-documentation/get-expired-historical-candle-data/.

### Expired-instruments historical data — earliest available date

**Could not be confirmed from Upstox's own documentation pages** (no stated coverage floor was found on https://upstox.com/developer/api-documentation/get-expired-historical-candle-data/, fetched directly — it does not state a minimum date). However, a user report on Upstox's own developer community forum states the practical floor is **2024-10-03**: "earliest 2024-10-03" for NIFTY F&O expiries, having tested back to 2022 and gotten empty arrays for anything older. An Upstox moderator (`Ushnota`) replied to this thread only with "We will surely check and get back to you on this" — **no official confirmation or denial of the 2024-10-03 floor was given** by Upstox staff in that thread.
Source: https://community.upstox.com/t/expired-instruments-api-no-data-before-oct-2024-and-docs-show-a-key-format-the-api-rejects-udapi1021/16626 (fetched directly). **This is a user-reported data point, not an Upstox-confirmed one — treat 2024-10-03 as "plausible practical floor, not officially verified."**

### 1minute interval — supported for the expired-instruments endpoint?

**Yes, confirmed from the official documentation page.** The Expired Historical Candle Data API documentation lists supported intervals as: `1minute, 3minute, 5minute, 15minute, 30minute, day`.
Source: https://upstox.com/developer/api-documentation/get-expired-historical-candle-data/ (fetched directly).

---

## 9. Could not verify from a primary source (explicit list)

- **Exact statutory text** of the Finance Act 2023, Finance (No. 2) Act 2024, and Budget 2026 speech/Finance Bill 2026 STT clauses — the correct `egazette.gov.in` / `indiabudget.gov.in` URLs were located, but the PDF fetch tooling used in this session could not extract machine-readable text from any of them (compressed/encoded PDF streams). Rates and effective dates for these three changes were corroborated instead via NSE circular references (which were themselves also not directly readable — see next point) and multiple independent secondary tax/broker sources quoting the same numbers and dates consistently.
- **Full text of NSE circulars** NSE/FATAX/56235 (2023-04-01), NSE/FATAX/63809 (2024-09-09), NSE/FATAX/73524 (2026-03-31), NSE/FA/64232 (transaction charges), NSE/CMPT/43079 (2020-01-01, stamp duty) — all located by URL/reference number via search, but direct fetches either failed to extract readable text (compressed PDF) or timed out repeatedly. Circular numbers, dates, and headline rates were corroborated via secondary sources quoting them, but the verbatim circular text was not personally read in this session.
- **Pre-2023 baseline STT rate of 0.01%** on futures sell-side — consistently cited by every secondary source describing the 2023 change, but the Finance Act/notification that originally set this rate (believed to be a 2013 change, predating the research window) was not independently traced or verified in this pass.
- **Pre-October-2024 NSE transaction-charge slab table** (exact volume thresholds and the full ₹1.73–₹1.88/lakh slab schedule) — only the approximate range was corroborated via secondary sources; the underlying NSE slab-rate circular was not located/read directly.
- **Whether the SEBI true-to-label reform was delayed for any component beyond 2024-10-01** (the task brief raised this as a possibility, citing reports of a "December 2024" delay) — no evidence of such a delay was found for equity index futures transaction charges specifically; 2024-10-01 is corroborated across NSE, SEBI, and Zerodha sources, but the absence of a delay elsewhere in the reform was not exhaustively checked.
- **Whether the SEBI Regulatory Fee on Stock Exchanges Regulations were amended again after the "last amended March 6, 2017" date shown on the current SEBI regulations page**, given secondary reports of a further 2019 amendment (SEBI/L.A.D.-N.R.O./G.N./2019/03, effective 2019-04-03) — the ₹10/crore rate itself is robustly corroborated as unchanged throughout 2020–2026 regardless of this discrepancy, but the discrepancy itself was not resolved.
- **PIB press release on the July 2020 stamp-duty rollout** (https://www.pib.gov.in/PressReleasePage.aspx?PRID=1635399) and the **PwC stamp-duty tax alert** — both URLs returned HTTP 403 on direct fetch in this session and were not independently read; relied on search-result snippets and Zerodha's corroborating explainer instead.
- **Exact historical NIFTY 50 closing index levels** for 2023-06-01, 2024-11-01, and 2025-06-01 — could not be pulled from a verified historical-data primary source (NSE's historical index data page was not successfully fetched; repeated timeouts). The worked example uses clearly-labeled illustrative approximations instead of verified closes.
- **Whether NSE equity index futures transaction charges were revised again at any point between 2024-10-01 and 2026-07-11** — no such revision was found, but this is a negative result from a finite set of searches, not an exhaustive audit of every NSE circular issued in that ~21-month window.
- **The exact date Zerodha's ₹20-flat/0.03% futures brokerage structure first took effect**, and confirmation it was never revised at any point within 2020–2026 — current sources consistently state no change, but no dated historical announcement was reviewed to confirm this held continuously through the whole window.
- **Upstox Plus / "API Plus" pricing** — confirmed no discrete subscription fee currently exists (it's free to activate, with a brokerage trade-off instead), based on an Upstox help-center article and a community-forum reply from an Upstox account; a formal Upstox pricing page stating "₹0" explicitly was not found (the pricing pages simply don't list a Plus subscription line item, which is consistent with, but not a direct printed confirmation of, "free").
- **Official confirmation of the 2024-10-03 floor for Upstox's expired-instruments historical data** — this is a user's empirical finding on Upstox's own community forum, met only with a non-committal "we'll check and get back to you" from a moderator, not an Upstox-authored statement of a data-coverage floor.
