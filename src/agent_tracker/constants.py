"""Shared constants for the Ellzaf Agent Tracker SDK."""

SDK_NAME = "agent-tracker-python"
SDK_VERSION = "0.1.0"
SDK_LANGUAGE = "python"
SCHEMA_VERSION = "2026-06-07"
REDACTION_VERSION = "2026-06-07"

DEFAULT_ENDPOINT = "https://api.ellzaf.com"
DEFAULT_QUEUE_DIR = ".ellzaf/queue"
DEFAULT_ENVIRONMENT = "paper"
DEFAULT_MAX_BATCH_EVENTS = 100
DEFAULT_MAX_BATCH_BYTES = 1_048_576
DEFAULT_MAX_EVENT_BYTES = 65_536
DEFAULT_MAX_QUEUE_BYTES = 50 * 1_048_576
DEFAULT_HTTP_TIMEOUT_SECONDS = 5.0
DEFAULT_FLUSH_INTERVAL_SECONDS = 30.0

SUPPORTED_ENVIRONMENTS = {
    "development",
    "paper",
    "shadow",
    "replay",
    "live_observe",
}

SUPPORTED_EVENT_TYPES = {
    "agent.run.started",
    "agent.run.completed",
    "agent.build.recorded",
    "llm.call.started",
    "llm.call.completed",
    "tool.call.completed",
    "source.claim.recorded",
    "market.snapshot.recorded",
    "memory.read.completed",
    "decision.proposed",
    "order.intent.recorded",
    "decision.outcome.recorded",
    "risk.check.completed",
    "trade.rejected",
    "paper.fill.recorded",
    "position.snapshot.recorded",
    "portfolio.snapshot.recorded",
    "capital.flow.recorded",
    "performance.snapshot.recorded",
    "strategy.context.recorded",
    "replay.result.recorded",
    "cost.usage.recorded",
    "error.recorded",
}

COMPLETION_STATUSES = {
    "succeeded",
    "failed",
    "cancelled",
    "abandoned",
    "partial",
    "schema_failed",
    "postcondition_failed",
}
