# MERIDIAN Control

## Purpose

MERIDIAN Control is the durable control plane for MERIDIAN research jobs. The MERIDIAN Python worker does the computation.

## Requirements

- Node 22+
- A running MERIDIAN Python control API and worker

## Environment

- `MERIDIAN_WORKER_URL`
- `MERIDIAN_WORKER_TOKEN`
- `MERIDIAN_APPROVAL_TOKEN`

## Commands

```text
npm run dev
npm run build
npm run test
npm run test:workflow
```

## Scope

Supported workflow types are `governance.bootstrap.v1` and `diagnostic.intraday-v1_1-smoke.v1`. Everything else is rejected.
