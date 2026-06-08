# Changelog

## 0.2.0 - 2026-06-08

- Added wrapper-first integration tools: `trace(..., flush_after=True)`,
  `wrap_agent`, `instrument`, domain wrappers, auto-flush helpers, and
  `doctor-repo --write-plan`.
- Added custom-agent coding prompt through
  `agent-tracker print-agent-prompt --profile custom`.
- Added Free, Basic, and Pro tier readiness checks.
- Added agentic-security readiness checks.
- Added deterministic local repair packs, dataset extraction, and eval plans.
- Added proof readiness, arena readiness, and experiment manifest artifacts.
- Added declarative custom log mapping through `agent-tracker map-events` for
  JSONL, JSON arrays, CSV, and read-only SQLite queries.
- Added typed payload builders for reporting-grade trade lifecycle, replay,
  prompt, build, and strategy events.
- Added queue upload locking, retry sidecar metadata, backoff-aware pending
  selection, `Retry-After` support, and last-upload health fields.
- Added opt-in local idempotency-key dedupe through
  `ELLZAF_DEDUPE_IDEMPOTENCY_KEYS`.
- Added reusable batch and upload-response contract fixtures for SDK and
  website ingestion CI.
- Added CLI commands: `tier-readiness`, `agentic-security-readiness`,
  `proof-readiness`, `arena-readiness`, `repair-pack`, `dataset-from-events`,
  `eval-plan`, `experiment-manifest`, and `map-events`.
- Added `py.typed` for type-aware users.
- Added CI wheel install smoke.

## 0.1.2 - 2026-06-08

- Added `flush_all()` and CLI `flush --drain`.
- Added `flush --dry-run` and `doctor-upload`.
- Added actionable upload summaries with status, reason codes, retry counts,
  and stop reasons.
- Added configurable gzip, local sampling, event budgets, and upload-byte
  budgets.
- Added explicit redaction for `ellzaf_trk_...` tracker ingestion keys.
- Improved queue health with pending age and failed/quarantined byte counts.
- Clarified endpoint configuration in the README.

## 0.1.1 - 2026-06-08

- Initial public package release workflow and SDK foundation.
