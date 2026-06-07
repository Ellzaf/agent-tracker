# Ellzaf Agent

Python SDK and local tooling for sending redacted telemetry from self-built AI
trading agents to Ellzaf.

Use Ellzaf Agent to record:

- agent runs and completion status
- prompt versions and model calls
- tool calls and research sources
- market snapshots and memory reads
- proposed decisions and risk checks
- rejected trades and paper fills
- replay results, costs, and errors

Ellzaf Agent observes your agent. It does not place orders, connect to a broker,
rank stocks, or produce buy and sell signals.

## Install

From this repository:

```bash
python -m pip install -e .
```

For development:

```bash
python -m pip install -e ".[dev]"
```

For the optional aitrade-style Postgres exporter:

```bash
python -m pip install -e ".[aitrade]"
```

## Configure

Set these environment variables before running your agent:

```bash
export ELLZAF_PROJECT="my-paper-agent"
export ELLZAF_API_KEY="your-project-ingestion-key"
```

Optional settings:

```bash
export ELLZAF_ENVIRONMENT="paper"
export ELLZAF_QUEUE_DIR=".ellzaf/queue"
export ELLZAF_TELEMETRY_ENABLED="true"
export ELLZAF_STORE_FULL_IO="false"
```

Supported environments:

- `development`
- `paper`
- `shadow`
- `replay`
- `live_observe`

## Quick Start

```python
from ellzaf_agent import Ellzaf

ellzaf = Ellzaf.from_env()

with ellzaf.run(run_type="portfolio_allocation", symbols=["NVDA", "MSFT"]) as run:
    run.prompt_version(
        family="allocation",
        version="2026-06-07",
        prompt_hash="sha256:...",
    )
    run.llm_call(
        provider="openai",
        model="example-model",
        input_hash="sha256:...",
        output_hash="sha256:...",
    )
    run.market_snapshot(
        source="local_bars",
        freshness_seconds=180,
        session="regular",
    )
    run.decision_proposed(
        decision_kind="target_weight",
        action="increase",
        symbol="NVDA",
        target_weight=0.15,
    )
    run.risk_check(approved=False, reasons=["max_position_pct"])
    run.final_action(action="no_order", reason="risk_gate_rejected")

ellzaf.flush()
```

## Manual Events

Use `event(...)` when your agent does not fit the helper methods:

```python
ellzaf.event(
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

## Mistake Fields

Add normalized fields when your repo already has the evidence:

```python
run.risk_check(
    approved=False,
    reasons=["cash_only_capacity_below_target_notional"],
    component="risk_gate",
    severity="critical",
    mistake_family="portfolio.buying_power_as_cash",
    money_impact="blocked",
    blocking_status="trading_blocked",
    resolution_status="open",
    next_safe_action="block_artifact",
    evidence_refs=[{"table": "risk_checks", "id": "risk_123"}],
)
```

The SDK validates bundled taxonomy values. Use `custom.<local_family>` for a
local failure mode that does not fit the built-in taxonomy.

## Run Helpers

Inside `with ellzaf.run(...) as run`, you can call:

- `run.prompt_version(...)`
- `run.llm_call(...)`
- `run.tool_call(...)`
- `run.source_claim(...)`
- `run.market_snapshot(...)`
- `run.memory_read(...)`
- `run.decision_proposed(...)`
- `run.risk_check(...)`
- `run.trade_rejected(...)`
- `run.paper_fill(...)`
- `run.portfolio_snapshot(...)`
- `run.replay_result(...)`
- `run.cost_usage(...)`
- `run.error(...)`
- `run.final_action(...)`

The SDK supports async context managers:

```python
async with ellzaf.arun(run_type="research_report", symbols=["AAPL"]) as run:
    run.source_claim(claim_type="financial_result", symbol="AAPL")
```

## Decorator

Use `trace(...)` around a function when you want a run per call:

```python
@ellzaf.trace(run_type="research_report", symbols=["AAPL"])
def build_report(symbol: str) -> dict:
    return {"symbol": symbol, "status": "done"}
```

The decorator records a run start and completion event. If the function raises,
the SDK records an error event and a failed completion event, then raises the
original exception.

## Privacy

Ellzaf Agent redacts events before it writes them to disk or uploads them.

Default behavior:

- prompts and model outputs become hashes with character counts
- API keys, bearer tokens, passwords, and common secret formats become
  `[REDACTED]`
- broker payloads and account identifiers become hashes
- bytes become hash and byte-count metadata
- non-finite floats become `null`

Set `ELLZAF_STORE_FULL_IO=true` to store prompt and output text. Secret
redaction runs before queueing and upload.

## Queue And Upload

The SDK writes one event per JSONL file under `.ellzaf/queue` by default.
`flush()` uploads a batch to Ellzaf with gzip and bearer-token authentication.

If the API key is missing, `flush()` leaves events in the local queue and
returns a skipped summary. If Ellzaf returns a retryable error, the SDK keeps the
event pending for a later flush.

```python
summary = ellzaf.flush()

print(summary.attempted)
print(summary.accepted)
print(summary.rejected)
print(summary.retryable)
```

## Disable Telemetry

Disable queue writes and uploads with:

```bash
export ELLZAF_TELEMETRY_ENABLED="false"
```

You can create local event objects through `ellzaf.event(...)`; the SDK
validates and returns them without writing queue files.

## CLI

```bash
ellzaf-agent init
ellzaf-agent doctor-repo --path .
ellzaf-agent print-agent-prompt --profile ebook
ellzaf-agent emit-sample --profile ebook --output ellzaf-sample.jsonl
ellzaf-agent validate-jsonl ellzaf-sample.jsonl
ellzaf-agent queue-health
ellzaf-agent flush
```

Only `flush` uses the network. The other commands inspect local files, print
package prompts, or validate JSONL.

## JSONL Export

Use `JsonlSink` for local audits, support bundles, and adapters:

```python
from ellzaf_agent import JsonlSink

sink = JsonlSink("ellzaf-events.jsonl")
sink.write(event)
```

The sink redacts, validates, and writes one event per line.

## Repo Doctor

```python
from ellzaf_agent.doctor import doctor_repo

report = doctor_repo(".")
print(report.to_dict())
```

The doctor reads the repo and reports coverage for ebook-style surfaces such as
sources, prompts, model calls, market data, decisions, risk gates, paper fills,
PnL, memory, shadow models, replay, cost, redaction, and backups. It does not
modify code.

## Aitrade-Style Export

Use the optional adapter when your repo stores starter-style rows:

```python
from ellzaf_agent.adapters.aitrade import AitradeExporter

exporter = AitradeExporter.from_database_url(database_url)
summary = exporter.export_jsonl("ellzaf-events.jsonl")
```

For tests, pass fixture rows without a database:

```python
events, summary = AitradeExporter().events_from_rows(rows_by_table)
```

## Integration Tests

Downstream repos can validate their emitted events with:

```python
from ellzaf_agent.testing import assert_valid_ellzaf_events

def test_ellzaf_events(events):
    assert_valid_ellzaf_events(events)
```

The helper checks schema rules, UTC timestamps, taxonomy values, privacy flags,
secret patterns, raw prompt/output leaks, raw broker payloads, raw account IDs,
and required event coverage when requested.

## Package Contract

The package ships JSON Schemas, taxonomies, valid fixtures, invalid fixtures,
and coding-agent prompts under `ellzaf_agent/schemas` and
`ellzaf_agent/prompts`. Backend ingestion services should validate against the
same schema version and taxonomy.

## Development

Run the test suite:

```bash
python -m pytest
python -m ruff check src tests
python -m build
```

The package has no runtime dependencies outside the Python standard library.

## Learn More

Buy the Blueprint For AI Trade ebook, reference code, or guided setup at
[ellzaf.com](https://ellzaf.com).
