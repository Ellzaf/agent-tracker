# Add Ellzaf Agent Tracker To An Ellzaf Reference-Code Repo

Use the row-based `AitradeExporter` first. Do not require a live database for
unit tests.

Target rows:

- `prompt_versions`
- `llm_runs`
- `llm_tool_calls`
- `search_usage_events`
- `research_reports`
- `source_quality_repair_incidents`
- `market_tape_snapshots`
- `market_regime_snapshots`
- `memory_fact_usage`
- `portfolio_allocation_runs`
- `portfolio_targets`
- `portfolio_rebalance_actions`
- `risk_checks`
- `order_intents`
- `trade_journal`
- `portfolio_snapshots`
- `portfolio_performance_scorecards`
- `decision_replay_runs`
- `harness_eval_runs`
- `harness_replay_runs`
- `shadow_allocation_runs`
- `shadow_order_fills`
- `shadow_profile_scorecards`

Add a thin command in the user repo only if the repo needs one. Prefer calling:

```python
from agent_tracker.adapters.aitrade import AitradeExporter

exporter = AitradeExporter.from_database_url(database_url)
summary = exporter.export_jsonl("ellzaf-events.jsonl")
```

Tests:

- fixture rows produce valid Ellzaf events
- stable row IDs produce stable event IDs and idempotency keys
- source-quality repair rows map to `source.truncated_or_missing_evidence` or
  `source.repair_loop`
- cash-only risk blocks map to `portfolio.buying_power_as_cash`
- flow-adjusted PnL rows keep external capital flow separate from trading PnL
- shadow scorecards expose failed cadence or isolation warnings
- raw prompts, raw outputs, account IDs, broker payloads, full paths, and
  secrets stay out of exported JSONL
