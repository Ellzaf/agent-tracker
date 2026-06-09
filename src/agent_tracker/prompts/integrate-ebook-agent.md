# Integrate Ellzaf Agent Tracker Into A User-Built Trading Agent

You are working inside a user-built AI trading-agent repo. Add Ellzaf Agent Tracker
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
- candidate boards, shortlists, review lanes, excluded candidates:
  `opportunity.board.recorded`, `opportunity.candidate.reviewed`
- setup regime, entry permission, false-breakout/falling-knife/range/trend
  profile: `setup.profile.recorded`
- target weights, watch/avoid actions, rebalance drafts: `decision.proposed`
- planned, skipped, clipped, rejected, or deferred optimizer actions:
  `action.outcome.recorded`
- order intent, planned side, planned quantity, intended price:
  `order.intent.recorded`
- decision result, no-order result, fill/reject/defer link:
  `decision.outcome.recorded`
- deterministic gates, cash-only checks, concentration, stale data: `risk.check.completed`
- blocked orders or skipped actions: `trade.rejected`
- paper or shadow fills: `paper.fill.recorded`
- account and portfolio summary: `portfolio.snapshot.recorded`
- per-position quantity, value, realized/unrealized PnL:
  `position.snapshot.recorded`
- deposits, withdrawals, transfers, fees, adjustments:
  `capital.flow.recorded`
- flow-adjusted PnL, return base, drawdown, session date:
  `performance.snapshot.recorded`
- strategy, setup, playbook, planned risk, regime context:
  `strategy.context.recorded`
- same-input model, prompt, or profile comparison epochs:
  `evaluation.epoch.started`, `evaluation.epoch.member.completed`
- build, prompt/config hash, risk-gate version: `agent.build.recorded`
- regression, replay, scenario, prompt, or risk-gate tests: `replay.result.recorded`
- search credits, tokens, provider spend: `cost.usage.recorded`
- source, market, memory, shadow, orchestration, privacy failures: `error.recorded`

Implementation steps:

1. Install `agent-tracker`.
2. Add environment variables in an example env file:
   `ELLZAF_PROJECT`, `ELLZAF_API_KEY`, `ELLZAF_ENVIRONMENT`,
   `ELLZAF_AGENT_ID`, `ELLZAF_QUEUE_DIR`, `ELLZAF_TELEMETRY_ENABLED`,
   `ELLZAF_STORE_FULL_IO`.
3. Create one small telemetry module that returns `AgentTracker.from_env()` and has a
   disabled/local mode for tests.
4. Instrument the smallest stable boundaries first: run start, model call,
   source claim, market snapshot, candidate board/review when present,
   decision, action outcome, order intent, risk check, rejected trade, fill,
   position, performance, replay, cost, and error.
5. Add `component`, `severity`, `mistake_family`, `money_impact`,
   `blocking_status`, `resolution_status`, `next_safe_action`, `evidence_refs`,
   and `correlation_ids` when the repo already has that evidence.
6. Add reporting-grade fields when the repo has them: `decision_id`,
   `order_intent_id`, `position_id`, `capital_flow_id`, `strategy_id`,
   `session_date`, `open_close_effect`, `fees`, and flow-adjusted PnL.
   Add diagnostic fields when present: `board_id`, `candidate_id`,
   `review_status`, `setup_profile_id`, `primary_regime`, `entry_permission`,
   `action_id`, `epoch_id`, `member_id`, `context_hash`, and `coverage_penalty`.
7. Add an integration report using `AgentTrackerIntegrationReport` and include tests
   with `assert_valid_agent_tracker_events`.
8. Run the repo tests plus `agent-tracker validate-jsonl` on any exported events.
9. If the repo should support hosted stats, run
   `agent-tracker validate-jsonl path/to/events.jsonl --profile strict-reporting`.

Use `custom.<local_family>` only when the repo has a real local failure mode
that does not fit the bundled taxonomy.
