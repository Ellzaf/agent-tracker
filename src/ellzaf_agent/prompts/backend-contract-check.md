# Check A Website Backend Against Ellzaf Agent

Build or review the receiving API against the SDK contract.

Endpoint:

```text
POST /v1/events/batch
Authorization: Bearer <project-ingestion-key>
Content-Type: application/json
Content-Encoding: gzip
Idempotency-Key: <batch-id>
```

Body:

```json
{
  "batch_id": "batch_example",
  "sent_at": "2026-06-07T00:00:00Z",
  "events": []
}
```

Requirements:

- Authenticate the project key before parsing large bodies.
- Enforce compressed and decompressed body limits.
- Validate every event with the same schema version and taxonomy as the SDK.
- Enforce idempotency by project and event ID.
- Keep tenant data isolated by key, project, agent, run, and event ID.
- Reject unknown built-in `mistake_family` values unless they use
  `custom.<local_family>`.
- Store privacy flags and do not log rejected raw payloads.
- Return accepted, duplicate, and rejected counts in the SDK response shape.
- Treat `money_impact=blocked`, `submitted`, `filled`, or `unknown` as high
  attention for dashboards and report cards.

Start dashboards with ingestion health, recent runs, event timeline, privacy
flags, mistake filters, replay readiness, evidence integrity, risk discipline,
market freshness, PnL accounting, memory lifecycle, shadow fairness,
orchestration health, and cost.
