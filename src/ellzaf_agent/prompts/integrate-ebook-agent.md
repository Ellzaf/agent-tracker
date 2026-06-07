# Integrate Ellzaf Agent Into An Ebook-Style Trading Agent

You are working inside a user-built AI trading-agent repo. Add Ellzaf Agent
telemetry without changing trading behavior.

Rules:

- Inspect the repo before editing.
- Keep broker execution, allocation math, risk gates, replay behavior, and
  provider choices unchanged.
- Do not add buy, sell, hold, ranking, or broker-order logic.
- Do not upload API keys, bearer tokens, passwords, raw broker payloads, raw
  account IDs, full local paths, hidden reasoning, raw prompts, or raw model
  outputs by default.
- Keep `ELLZAF_STORE_FULL_IO=false` unless the owner explicitly enables it.
- Add tests that prove events validate while telemetry is disabled, queued
  locally, and uploaded through a mocked transport.

Map local concepts to these Ellzaf events:

- watchlist or universe scope: `market.snapshot.recorded`
- source search, extraction, citations, and source quality: `tool.call.completed`,
  `source.claim.recorded`, `cost.usage.recorded`, `error.recorded`
- prompt version, prompt hash, provider, model, mode: `llm.call.started`
- model result, schema validation, postconditions, token use: `llm.call.completed`
- market bars, tape, regime, freshness, scope: `market.snapshot.recorded`
- memory reads, context packs, lifecycle, supersession: `memory.read.completed`
- target weights, watch/avoid actions, rebalance drafts: `decision.proposed`
- deterministic gates, cash-only checks, concentration, stale data: `risk.check.completed`
- blocked orders or skipped actions: `trade.rejected`
- paper or shadow fills: `paper.fill.recorded`
- account, positions, PnL, scorecards: `portfolio.snapshot.recorded`
- regression, replay, scenario, prompt, or risk-gate tests: `replay.result.recorded`
- search credits, tokens, provider spend: `cost.usage.recorded`
- source, market, memory, shadow, orchestration, privacy failures: `error.recorded`

Implementation steps:

1. Install `ellzaf-agent`.
2. Add environment variables in an example env file:
   `ELLZAF_PROJECT`, `ELLZAF_API_KEY`, `ELLZAF_ENVIRONMENT`,
   `ELLZAF_AGENT_ID`, `ELLZAF_QUEUE_DIR`, `ELLZAF_TELEMETRY_ENABLED`,
   `ELLZAF_STORE_FULL_IO`.
3. Create one small telemetry module that returns `Ellzaf.from_env()` and has a
   disabled/local mode for tests.
4. Instrument the smallest stable boundaries first: run start, model call,
   source claim, market snapshot, decision, risk check, rejected trade, replay,
   cost, and error.
5. Add `component`, `severity`, `mistake_family`, `money_impact`,
   `blocking_status`, `resolution_status`, `next_safe_action`, `evidence_refs`,
   and `correlation_ids` when the repo already has that evidence.
6. Add an integration report using `EllzafIntegrationReport` and include tests
   with `assert_valid_ellzaf_events`.
7. Run the repo tests plus `ellzaf-agent validate-jsonl` on any exported events.

Use `custom.<local_family>` only when the repo has a real local failure mode
that does not fit the bundled taxonomy.
