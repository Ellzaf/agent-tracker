# Ellzaf Agent Tracker

Ellzaf Agent Tracker is a Python SDK for AI trading agents.

Install it in your agent repo, send redacted telemetry to Ellzaf, and use that
data for engineering diagnostics, trading-agent statistics, replay checks, and
repair prompts.

Ellzaf looks at how your agent behaves:

- what it read before a decision;
- which model and prompt version it used;
- what trade or allocation it wanted to make;
- which risk gate allowed, changed, or blocked the action;
- what happened in paper trading, shadow trading, or replay;
- where the agent drifted, used stale data, missed sources, or broke tests.

Ellzaf Agent Tracker does not place broker orders, rank stocks, generate buy or sell
signals, or replace your risk gates. Your agent remains in control of its own
logic. Ellzaf observes the system and reports engineering, safety, and data
quality issues.

## Who This Is For

Use this package if you are building, testing, or maintaining a Python AI
trading agent.

It can work with most agent designs after you map your existing objects to
Ellzaf events. Your repo may use OpenAI, Anthropic, LangChain, Agno, a custom
loop, a Postgres journal, JSON files, notebooks, or plain Python classes. The
SDK only needs structured facts about the run.

You get the smoothest setup if your agent follows the Ellzaf ebook, uses the
Ellzaf reference code, or was installed through an Ellzaf setup. Those projects
already have the same concepts Ellzaf expects: prompts, source checks, market
snapshots, decisions, risk gates, paper fills, replay tests, and performance
tracking.

Learn the full system, buy the reference code, or buy a guided setup at
[ellzaf.com](https://ellzaf.com).

## What You Send

Start small. A useful integration records one run, one model call, one decision,
one risk check, and one outcome.

Add richer events when you want hosted stats and weekly repair prompts.

| Your Agent Has | Send To Ellzaf |
| --- | --- |
| Agent run or workflow | `agent.run.started`, `agent.run.completed` |
| Prompt version, model, provider | `llm.call.started`, `llm.call.completed` |
| Search, tools, citations | `tool.call.completed`, `source.claim.recorded` |
| Market data and freshness | `market.snapshot.recorded` |
| Memory or context reads | `memory.read.completed` |
| Proposed allocation or action | `decision.proposed` |
| Planned order before execution | `order.intent.recorded` |
| Risk checks and blocked actions | `risk.check.completed`, `trade.rejected` |
| Paper, shadow, or replay fills | `paper.fill.recorded` |
| Positions and portfolio state | `position.snapshot.recorded`, `portfolio.snapshot.recorded` |
| Deposits, withdrawals, fees | `capital.flow.recorded` |
| P&L, returns, drawdown | `performance.snapshot.recorded` |
| Replay or regression tests | `replay.result.recorded` |
| Strategy, setup, market regime | `strategy.context.recorded` |
| Build, config, risk-gate version | `agent.build.recorded` |
| Cost and errors | `cost.usage.recorded`, `error.recorded` |

## Install

Install in the same Python environment as your trading agent:

```bash
python -m pip install agent-tracker
```

From this repository:

```bash
python -m pip install -e .
```

For local SDK development:

```bash
python -m pip install -e ".[dev]"
```

For the optional exporter used by Ellzaf reference-code databases:

```bash
python -m pip install -e ".[aitrade]"
```

## Configure

Create a starter env file:

```bash
agent-tracker init
```

Then set your project values:

```bash
export ELLZAF_PROJECT="your-dashboard-project-slug"
export ELLZAF_API_KEY="your-tracker-ingestion-key"
export ELLZAF_ENVIRONMENT="paper"
export ELLZAF_AGENT_ID="local-agent"
```

Use the **Project slug** shown in the Ellzaf Monitoring dashboard for
`ELLZAF_PROJECT`. Do not use the display name if it differs from the slug. Use
the Tracker ingestion key shown once when you create or rotate the project key
for `ELLZAF_API_KEY`.

Common optional settings:

```bash
export ELLZAF_QUEUE_DIR=".ellzaf/queue"
export ELLZAF_TELEMETRY_ENABLED="true"
export ELLZAF_STORE_FULL_IO="false"
export ELLZAF_GZIP="true"
export ELLZAF_SAMPLE_RATE="1.0"
```

The default base endpoint is `https://ellzaf.com`. The SDK uploads batches to
`https://ellzaf.com/v1/events/batch`. Only set `ELLZAF_ENDPOINT` if Ellzaf
support gives you a different base URL.

Supported environments:

- `development`
- `paper`
- `shadow`
- `replay`
- `live_observe`

Keep `ELLZAF_STORE_FULL_IO=false` unless you want to store prompt and model
output text. The default sends hashes and character counts instead.

Optional local volume controls:

```bash
export ELLZAF_MAX_EVENTS_PER_RUN=""
export ELLZAF_MAX_EVENTS_PER_DAY=""
export ELLZAF_MAX_UPLOAD_BYTES_PER_DAY=""
```

Leave these blank unless your agent is high volume. Errors, failed risk checks,
rejected trades, fills, portfolio snapshots, performance snapshots, and replay
results are preserved by default even when sampling is enabled.

## Quick Start

This example records a decision that the risk gate blocks. Ellzaf can later use
that trace to explain the block, detect stale inputs, and suggest tests or code
changes.

```python
from agent_tracker import AgentTracker

tracker = AgentTracker.from_env()

with tracker.run(run_type="portfolio_allocation", symbols=["NVDA", "MSFT"]) as run:
    run.prompt_version(
        family="allocation",
        version="2026-06-07",
        prompt_hash="sha256:...",
        provider="openai",
        model="example-model",
    )

    run.market_snapshot(
        source="local_bars",
        freshness_seconds=180,
        session_state="regular",
    )

    run.decision_proposed(
        decision_id="decision_1",
        decision_kind="target_weight",
        action="increase",
        symbol="NVDA",
        target_weight="0.15",
    )

    order = run.order_intent(
        order_intent_id="intent_1",
        decision_id="decision_1",
        symbol="NVDA",
        side="buy",
        intended_quantity="2",
        intended_price="100.00",
        open_close_effect="open",
        session_date="2026-06-07",
    )

    run.risk_check(
        approved=False,
        reasons=["max_position_pct"],
        component="risk_gate",
        severity="warning",
        mistake_family="custom.max_position_pct_block",
        next_safe_action="observe",
    )

    run.decision_outcome(
        decision_id="decision_1",
        outcome_kind="no_order",
        linked_event_ids=[order["event_id"]],
        changed_by_risk_gate=True,
    )

    run.final_action(action="no_order", reason="risk_gate_rejected")

tracker.flush_all()
```

For a smaller transition, wrap one function and let the SDK flush after the run:

```python
from agent_tracker import AgentTracker

tracker = AgentTracker.from_env()

@tracker.trace(run_type="portfolio_allocation", flush_after=True)
def run_agent() -> None:
    ...
```

You can also wrap existing functions that already return structured results:

```python
safe_risk_gate = tracker.wrap_risk_gate(
    risk_gate.validate,
    approved=lambda result: result.approved,
    reasons=lambda result: result.reasons,
)

tracked_decision = tracker.wrap_decision(
    agent.decide,
    decision_kind="target_weight",
    action=lambda result: result.action,
    symbol=lambda result: result.symbol,
)
```

These wrappers preserve the wrapped function's return value and exception
behavior. Uploads should still be mocked in tests.

## Add Trading Stats

Ellzaf needs trade lifecycle and account context to compute useful stats. Add
these events when your agent has the data.

```python
with tracker.run(run_type="paper_fill", symbols=["NVDA"]) as run:
    run.paper_fill(
        fill_id="fill_1",
        position_id="pos_1",
        order_intent_id="intent_1",
        symbol="NVDA",
        side="sell",
        open_close_effect="close",
        quantity="2",
        price="101.00",
        fees="0.25",
        currency="USD",
        fill_source="paper",
        session_date="2026-06-07",
        strategy_id="strat_breakout",
        setup="gap_hold",
    )

    run.position_snapshot(
        portfolio_kind="paper",
        position_id="pos_1",
        symbol="NVDA",
        quantity="0",
        realized_pnl="9.75",
    )

    run.performance_snapshot(
        period_kind="daily",
        period_start="2026-06-07",
        period_end="2026-06-07",
        session_date="2026-06-07",
        trading_pnl_amount="9.75",
        net_pnl_amount="9.75",
        fees="0.25",
        flow_adjusted_equity_change="9.75",
        return_base="1000.00",
        compounded_return_pct="0.98",
        max_drawdown_pct="1.2",
    )
```

Run the readiness check against exported JSONL:

```bash
agent-tracker validate-jsonl ellzaf-events.jsonl --profile strict-reporting
agent-tracker reporting-readiness ellzaf-events.jsonl
```

The readiness report tells you which dashboards Ellzaf can compute from your
data and which fields your agent still needs to send.

## Use A Coding Agent To Integrate

This package ships prompts for Codex, Claude Code, and similar coding agents.
Run the prompt command inside the repo you want to instrument:

```bash
agent-tracker print-agent-prompt --profile ebook
```

The `ebook` profile is for agents built from Ellzaf lessons, the Ellzaf
reference code, or a similar local trading-agent architecture.

For a repo review after integration:

```bash
agent-tracker print-agent-prompt --profile review
```

For a custom Python trading agent:

```bash
agent-tracker print-agent-prompt --profile custom
agent-tracker doctor-repo --path . --write-plan agent-tracker-plan.md
```

For backend ingestion teams:

```bash
agent-tracker print-agent-prompt --profile backend
```

## Manual Events

Use `event(...)` when helper methods do not match your code.

```python
tracker.event(
    "risk.check.completed",
    run_id="run_example",
    symbols=["NVDA"],
    payload={
        "risk_check_kind": "deterministic",
        "approved": False,
        "reasons": ["stale_market_data"],
    },
)
```

The SDK validates the event before it writes to disk or uploads.

## Local JSONL Export

Use `JsonlSink` for local audits, support bundles, or custom adapters.

```python
from agent_tracker import JsonlSink

sink = JsonlSink("ellzaf-events.jsonl")
sink.write(event)
```

The sink redacts, validates, and writes one event per line.

Generate sample files:

```bash
agent-tracker emit-sample --profile ebook --output ellzaf-sample.jsonl
agent-tracker emit-sample --profile reporting --output ellzaf-reporting.jsonl
```

Validate any file before you upload or share it:

```bash
agent-tracker validate-jsonl ellzaf-sample.jsonl
agent-tracker validate-jsonl ellzaf-reporting.jsonl --profile strict-reporting
```

## Reference-Code Exporter

Some Ellzaf projects store telemetry-like rows in a Postgres database. Use the
optional exporter when your repo has those tables or close equivalents:

```python
from agent_tracker.adapters.aitrade import AitradeExporter

exporter = AitradeExporter.from_database_url(database_url)
summary = exporter.export_jsonl("ellzaf-events.jsonl")
```

For tests, pass rows without a database:

```python
events, summary = AitradeExporter().events_from_rows(rows_by_table)
```

If your table names or fields differ, write a thin adapter that emits the same
Ellzaf event types. Most custom agents only need small mapping changes.

## Privacy And Safety

Ellzaf Agent Tracker redacts events before queueing or upload.

Default behavior:

- prompt and model output fields become hashes with character counts;
- API keys, bearer tokens, passwords, and common secret patterns become
  `[REDACTED]`;
- broker payloads and account identifiers become hashes;
- bytes become hash and byte-count metadata;
- non-finite numbers such as `NaN` and `Infinity` are rejected or converted to
  safe JSON values before upload.

The SDK does not call brokers, read broker quotes, or create orders.

## Queue And Upload

The SDK writes one event per JSONL file under `.ellzaf/queue` by default.
`flush()` uploads one batch to Ellzaf with gzip and bearer-token
authentication. `flush_all()` drains the queue until it is empty, skipped, or a
retryable error needs a later attempt.

```python
summary = tracker.flush()
summary = tracker.flush_all()

print(summary.attempted)
print(summary.accepted)
print(summary.rejected)
print(summary.retryable)
print(summary.reason_code)
print(summary.stop_reason)
```

If the API key is missing, `flush()` leaves events in the local queue and
returns a skipped summary. If Ellzaf returns a retryable error, the SDK keeps
the event pending for a later flush.

Check upload configuration without moving queue files:

```bash
agent-tracker flush --dry-run
agent-tracker flush --drain --dry-run
```

Run an isolated diagnostic check:

```bash
agent-tracker doctor-upload
```

By default `doctor-upload` prepares a diagnostic batch without using the
network. Pass `--live` only when you want to send the diagnostic event to
Ellzaf.

Disable queue writes and uploads:

```bash
export ELLZAF_TELEMETRY_ENABLED="false"
```

You can still create and validate event objects with telemetry disabled.

## Test Your Integration

Add a test in your agent repo:

```python
from agent_tracker.testing import assert_valid_agent_tracker_events


def test_agent_tracker_events(events):
    assert_valid_agent_tracker_events(events)
```

Use stricter profiles when your repo should support hosted stats, arena scoring,
or proof pages:

```python
assert_valid_agent_tracker_events(events, profile="strict-reporting")
assert_valid_agent_tracker_events(events, profile="strict-arena")
assert_valid_agent_tracker_events(events, profile="strict-proof")
```

The helper checks schema rules, UTC timestamps, taxonomy values, privacy flags,
secret patterns, raw prompt/output leaks, raw broker payloads, raw account IDs,
and required event coverage.

## CLI Reference

```bash
agent-tracker init
agent-tracker doctor-repo --path .
agent-tracker doctor-repo --path . --write-plan agent-tracker-plan.md
agent-tracker print-agent-prompt --profile ebook
agent-tracker print-agent-prompt --profile custom
agent-tracker print-agent-prompt --profile review
agent-tracker print-agent-prompt --profile backend
agent-tracker emit-sample --profile ebook --output ellzaf-sample.jsonl
agent-tracker emit-sample --profile reporting --output ellzaf-reporting.jsonl
agent-tracker validate-jsonl ellzaf-sample.jsonl
agent-tracker validate-jsonl ellzaf-reporting.jsonl --profile strict-reporting
agent-tracker reporting-readiness ellzaf-reporting.jsonl
agent-tracker queue-health
agent-tracker flush
agent-tracker flush --drain
agent-tracker flush --dry-run
agent-tracker doctor-upload
```

Only `flush` and `doctor-upload --live` use the network. The other commands
inspect local files, print package prompts, validate JSONL, or prepare dry-run
batches.

## Development

Run the package checks:

```bash
python -m pytest
python -m ruff check src tests
python -m build
```

The package has no runtime dependencies outside the Python standard library.

## Learn With Ellzaf

Ellzaf teaches the full AI trading-agent build at
[ellzaf.com](https://ellzaf.com).

You can buy:

- the Blueprint For AI Trade ebook;
- the reference AI trading-agent code;
- a guided setup if you want Ellzaf to help you install and configure the
  system.

Agents built from those materials need fewer integration changes because they
already follow the telemetry surfaces this SDK expects.
