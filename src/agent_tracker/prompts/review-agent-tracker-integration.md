# Review An Ellzaf Agent Tracker Integration

Review the repo as a safety and observability change. Lead with bugs and
behavioral risks.

Check:

- Trading behavior did not change.
- Broker order placement did not appear.
- Risk gates stayed deterministic.
- Replay and tests do not call live broker, market-data, search, or LLM
  providers unless the test explicitly mocks them.
- Events validate with `assert_valid_agent_tracker_events`.
- `ELLZAF_STORE_FULL_IO` stays false by default.
- Raw prompts, outputs, account IDs, broker payloads, local paths, API keys,
  tokens, and hidden reasoning do not appear in queued or exported events.
- Every event has stable `run_id`, `event_id`, `idempotency_key`,
  `occurred_at`, `symbols`, `payload`, `privacy`, and `sdk`.
- Mistake fields use the bundled taxonomy or `custom.<local_family>`.
- Rejected risk checks include reasons.
- Reporting-grade telemetry links decisions, order intents, fills, positions,
  capital flows, performance snapshots, strategy context, prompt hashes, and
  replay results when the repo claims hosted stats support.
- Opportunity-diagnostic telemetry records candidate boards, candidate review
  statuses, setup regimes, action outcomes, and fair evaluation epochs when
  the repo has those concepts.
- Source-quality, market freshness, cash-only risk, PnL, memory lifecycle,
  shadow fairness, prompt drift, replay isolation, cost, and privacy failures
  have coverage where the repo has those concepts.

Run:

```bash
python -m pytest
agent-tracker doctor-repo --path .
agent-tracker validate-jsonl path/to/events.jsonl
agent-tracker reporting-readiness path/to/events.jsonl
```

Report file and line references for any issue.
