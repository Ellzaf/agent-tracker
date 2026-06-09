# Integrate Agent Tracker Into A Custom Python Trading Agent

You are working in a Python AI trading-agent repository.

Install and instrument `agent-tracker` without changing trading behavior.

Rules:

- Preserve the agent's existing decisions, risk checks, order-intent logic, and
  paper-trading behavior.
- Do not add broker execution.
- Do not weaken deterministic risk gates.
- Do not call live broker quotes as a hidden telemetry dependency.
- Do not upload raw prompts, raw model outputs, broker payloads, account
  identifiers, API keys, full local paths, raw chat IDs, or hidden reasoning.
- Keep `ELLZAF_STORE_FULL_IO=false` unless the user explicitly asks otherwise.
- Use mocked upload tests. Do not hit the network in tests.

Work:

1. Run `agent-tracker doctor-repo --path . --write-plan agent-tracker-plan.md`.
2. Read the generated plan.
3. Add `AgentTracker.from_env()` at the agent boundary.
4. Prefer `@tracker.trace(...)`, `tracker.instrument(...)`, or helper methods
   around existing functions before writing custom plumbing.
5. Emit events for the surfaces this repo actually has: runs, model calls,
   tools, sources, market snapshots, memory reads, candidate boards/reviews,
   setup profiles, decisions, action outcomes, risk checks, rejected trades,
   paper fills, positions, portfolio snapshots, capital flows, performance
   snapshots, evaluation epochs, replay results, costs, and errors.
6. Add tests for telemetry disabled, local queue mode, mocked upload, privacy
   redaction, and JSONL validation.
7. Run `agent-tracker flush --dry-run` before any real upload.

Definition of done:

- Existing trading tests still pass.
- New telemetry tests pass.
- `agent-tracker validate-jsonl` passes for any exported JSONL.
- No secrets or account identifiers appear in events, docs, fixtures, or test
  snapshots.
