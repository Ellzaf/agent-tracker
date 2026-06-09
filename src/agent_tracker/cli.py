"""Command line interface for Ellzaf Agent Tracker."""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agent_tracker.adapters.aitrade import AitradeExporter
from agent_tracker.client import AgentTracker
from agent_tracker.config import Config
from agent_tracker.constants import (
    DEFAULT_MAX_QUEUE_BYTES,
    DEFAULT_QUEUE_DIR,
    SDK_VERSION,
)
from agent_tracker.doctor import doctor_repo, format_doctor_plan, format_doctor_report
from agent_tracker.errors import AgentTrackerError
from agent_tracker.mapping import export_mapped_events
from agent_tracker.queue import LocalQueue
from agent_tracker.reporting import (
    assess_agentic_security_readiness,
    assess_arena_readiness,
    assess_decision_flow_readiness,
    assess_proof_readiness,
    assess_reporting_readiness,
    assess_tier_readiness,
    build_dataset_items,
    build_decision_flow_diagnostic_events,
    build_eval_plan,
    build_experiment_manifest,
    build_repair_pack,
)
from agent_tracker.resources import (
    list_resource_names,
    read_json_resource,
    read_text_resource,
)
from agent_tracker.serialization import strict_json_dumps, strict_json_loads
from agent_tracker.sink import JsonlSink, read_jsonl_events
from agent_tracker.testing import assert_valid_agent_tracker_events

_PROMPT_PROFILES = {
    "ebook": "integrate-ebook-agent.md",
    "custom": "integrate-custom-agent.md",
    "aitrade": "add-aitrade-adapter.md",
    "review": "review-agent-tracker-integration.md",
    "backend": "backend-contract-check.md",
}
_VALIDATION_PROFILES = {
    "ebook",
    "aitrade",
    "strict-base",
    "strict-diagnostics",
    "strict-reporting",
    "strict-arena",
    "strict-proof",
}


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (AgentTrackerError, OSError, AssertionError, ValueError) as exc:
        print(f"agent-tracker: {exc}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-tracker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="write .env.ellzaf.example")
    init_parser.add_argument("--path", default=".env.ellzaf.example")
    init_parser.add_argument("--force", action="store_true")
    init_parser.set_defaults(func=_cmd_init)

    validate = subparsers.add_parser("validate-jsonl", help="validate a JSONL export")
    validate.add_argument("path")
    validate.add_argument("--profile", choices=sorted(_VALIDATION_PROFILES))
    validate.add_argument("--allow-full-io", action="store_true")
    validate.add_argument("--strict-mistakes", action="store_true")
    validate.set_defaults(func=_cmd_validate_jsonl)

    readiness = subparsers.add_parser(
        "reporting-readiness", help="show reporting data-quality readiness"
    )
    readiness.add_argument("path")
    readiness.add_argument("--allow-full-io", action="store_true")
    readiness.set_defaults(func=_cmd_reporting_readiness)

    tier_readiness = subparsers.add_parser(
        "tier-readiness", help="show Free, Basic, and Pro data readiness"
    )
    tier_readiness.add_argument("path")
    tier_readiness.add_argument("--allow-full-io", action="store_true")
    tier_readiness.set_defaults(func=_cmd_tier_readiness)

    decision_flow_readiness = subparsers.add_parser(
        "decision-flow-readiness",
        help="show decision-flow diagnostic readiness",
    )
    decision_flow_readiness.add_argument("path")
    decision_flow_readiness.add_argument("--allow-full-io", action="store_true")
    decision_flow_readiness.set_defaults(func=_cmd_decision_flow_readiness)

    diagnose = subparsers.add_parser(
        "diagnose",
        help="build local diagnostic check events from a JSONL export",
    )
    diagnose.add_argument("path")
    diagnose.add_argument("--output")
    diagnose.add_argument("--allow-full-io", action="store_true")
    diagnose.add_argument(
        "--fail-on",
        choices=["never", "warning", "failed"],
        default="failed",
        help="return a non-zero exit code when generated diagnostics reach this level",
    )
    diagnose.set_defaults(func=_cmd_diagnose)

    security_readiness = subparsers.add_parser(
        "agentic-security-readiness",
        help="show agentic security telemetry readiness",
    )
    security_readiness.add_argument("path")
    security_readiness.add_argument("--allow-full-io", action="store_true")
    security_readiness.set_defaults(func=_cmd_agentic_security_readiness)

    proof_readiness = subparsers.add_parser(
        "proof-readiness", help="show proof page and trust badge readiness"
    )
    proof_readiness.add_argument("path")
    proof_readiness.add_argument("--allow-full-io", action="store_true")
    proof_readiness.set_defaults(func=_cmd_proof_readiness)

    arena_readiness = subparsers.add_parser(
        "arena-readiness", help="show benchmark challenge readiness"
    )
    arena_readiness.add_argument("path")
    arena_readiness.add_argument("--allow-full-io", action="store_true")
    arena_readiness.set_defaults(func=_cmd_arena_readiness)

    repair_pack = subparsers.add_parser(
        "repair-pack", help="build a local deterministic repair evidence pack"
    )
    repair_pack.add_argument("path")
    repair_pack.add_argument("--output")
    repair_pack.add_argument("--prompt-output")
    repair_pack.add_argument("--max-findings", type=int, default=5)
    repair_pack.add_argument("--allow-full-io", action="store_true")
    repair_pack.set_defaults(func=_cmd_repair_pack)

    dataset = subparsers.add_parser(
        "dataset-from-events", help="create sanitized dataset JSONL from events"
    )
    dataset.add_argument("path")
    dataset.add_argument("--output", required=True)
    dataset.add_argument("--allow-full-io", action="store_true")
    dataset.set_defaults(func=_cmd_dataset_from_events)

    eval_plan = subparsers.add_parser(
        "eval-plan", help="create a deterministic eval plan from events"
    )
    eval_plan.add_argument("path")
    eval_plan.add_argument("--output")
    eval_plan.add_argument("--allow-full-io", action="store_true")
    eval_plan.set_defaults(func=_cmd_eval_plan)

    experiment = subparsers.add_parser(
        "experiment-manifest", help="create a deterministic experiment manifest"
    )
    experiment.add_argument("--from-repair-pack", required=True)
    experiment.add_argument("--output")
    experiment.add_argument(
        "--change",
        action="append",
        default=[],
        help="declared change as key=value; may be passed more than once",
    )
    experiment.set_defaults(func=_cmd_experiment_manifest)

    queue = subparsers.add_parser("queue-health", help="show local queue health")
    queue.add_argument("--queue-dir", default=DEFAULT_QUEUE_DIR)
    queue.add_argument("--max-queue-bytes", type=int, default=DEFAULT_MAX_QUEUE_BYTES)
    queue.add_argument("--max-batch-events", type=int)
    queue.set_defaults(func=_cmd_queue_health)

    flush = subparsers.add_parser("flush", help="flush the configured local queue")
    flush.add_argument("--project")
    flush.add_argument("--environment")
    flush.add_argument("--agent-id")
    flush.add_argument("--drain", action="store_true")
    flush.add_argument("--dry-run", action="store_true")
    flush.add_argument("--max-batches", type=int)
    flush.add_argument("--fail-fast", action="store_true")
    flush.set_defaults(func=_cmd_flush)

    doctor_upload = subparsers.add_parser(
        "doctor-upload",
        help="check upload configuration with an isolated diagnostic event",
    )
    doctor_upload.add_argument("--project")
    doctor_upload.add_argument("--environment")
    doctor_upload.add_argument("--agent-id")
    doctor_upload.add_argument(
        "--live",
        action="store_true",
        help="upload the diagnostic batch instead of only preparing it",
    )
    doctor_upload.add_argument("--fail-fast", action="store_true")
    doctor_upload.set_defaults(func=_cmd_doctor_upload)

    canary = subparsers.add_parser(
        "canary",
        help="send or dry-run a privacy-safe ingestion canary event",
    )
    canary.add_argument("--project")
    canary.add_argument("--environment")
    canary.add_argument("--agent-id")
    canary.add_argument(
        "--live",
        action="store_true",
        help="upload the canary batch instead of only preparing it",
    )
    canary.add_argument("--fail-fast", action="store_true")
    canary.set_defaults(func=_cmd_canary)

    sample = subparsers.add_parser("emit-sample", help="emit bundled sample events")
    sample.add_argument("--profile", choices=["ebook", "reporting"], default="ebook")
    sample.add_argument("--output", default="-")
    sample.set_defaults(func=_cmd_emit_sample)

    doctor = subparsers.add_parser("doctor-repo", help="inspect a repo for coverage")
    doctor.add_argument("--path", default=".")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--write-plan")
    doctor.set_defaults(func=_cmd_doctor_repo)

    prompt = subparsers.add_parser("print-agent-prompt", help="print an agent prompt")
    prompt.add_argument("--profile", choices=sorted(_PROMPT_PROFILES), default="ebook")
    prompt.set_defaults(func=_cmd_print_agent_prompt)

    export = subparsers.add_parser("export-aitrade", help="export aitrade rows")
    export.add_argument("--rows-json")
    export.add_argument("--database-url")
    export.add_argument("--output", required=True)
    export.add_argument("--project", default="aitrade")
    export.add_argument("--agent-id", default="aitrade")
    export.add_argument("--environment", default="paper")
    export.add_argument("--limit-per-table", type=int, default=500)
    export.set_defaults(func=_cmd_export_aitrade)

    map_events = subparsers.add_parser(
        "map-events", help="export events from a declarative TOML or JSON mapping"
    )
    map_events.add_argument("--config", required=True)
    map_events.add_argument("--output", required=True)
    map_events.set_defaults(func=_cmd_map_events)

    return parser


def _cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        raise ValueError(f"{path} already exists; pass --force to replace it")
    path.write_text(
        "\n".join(
            [
                "# Use the Project slug shown in the Ellzaf Monitoring dashboard.",
                'ELLZAF_PROJECT="your-dashboard-project-slug"',
                "# Use the Tracker ingestion key shown once in the dashboard.",
                'ELLZAF_API_KEY=""',
                'ELLZAF_ENVIRONMENT="paper"',
                'ELLZAF_AGENT_ID="local-agent"',
                'ELLZAF_QUEUE_DIR=".ellzaf/queue"',
                'ELLZAF_TELEMETRY_ENABLED="true"',
                'ELLZAF_STORE_FULL_IO="false"',
                'ELLZAF_GZIP="true"',
                'ELLZAF_SAMPLE_RATE="1.0"',
                "# Optional local safety limits. Leave blank unless needed.",
                'ELLZAF_MAX_EVENTS_PER_RUN=""',
                'ELLZAF_MAX_EVENTS_PER_DAY=""',
                'ELLZAF_MAX_UPLOAD_BYTES_PER_DAY=""',
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(path)
    return 0


def _cmd_validate_jsonl(args: argparse.Namespace) -> int:
    events = read_jsonl_events(args.path)
    assert_valid_agent_tracker_events(
        events,
        profile=args.profile,
        allow_full_io=args.allow_full_io,
        require_mistake_family_for_mistakes=args.strict_mistakes,
    )
    result: dict[str, Any] = {"valid": True, "event_count": len(events)}
    if args.profile in {"strict-reporting", "strict-arena", "strict-proof"}:
        result["reporting_readiness"] = assess_reporting_readiness(events).to_dict()
    if args.profile == "strict-diagnostics":
        result["decision_flow_readiness"] = assess_decision_flow_readiness(
            events
        ).to_dict()
    print(strict_json_dumps(result))
    return 0


def _cmd_reporting_readiness(args: argparse.Namespace) -> int:
    events = read_jsonl_events(args.path)
    assert_valid_agent_tracker_events(events, allow_full_io=args.allow_full_io)
    print(strict_json_dumps(assess_reporting_readiness(events).to_dict()))
    return 0


def _cmd_tier_readiness(args: argparse.Namespace) -> int:
    events = read_jsonl_events(args.path)
    assert_valid_agent_tracker_events(events, allow_full_io=args.allow_full_io)
    print(strict_json_dumps(assess_tier_readiness(events).to_dict()))
    return 0


def _cmd_decision_flow_readiness(args: argparse.Namespace) -> int:
    events = read_jsonl_events(args.path)
    assert_valid_agent_tracker_events(events, allow_full_io=args.allow_full_io)
    print(strict_json_dumps(assess_decision_flow_readiness(events).to_dict()))
    return 0


def _cmd_diagnose(args: argparse.Namespace) -> int:
    events = read_jsonl_events(args.path)
    assert_valid_agent_tracker_events(events, allow_full_io=args.allow_full_io)
    diagnostics = build_decision_flow_diagnostic_events(events)
    assert_valid_agent_tracker_events(diagnostics, allow_full_io=args.allow_full_io)
    readiness = assess_decision_flow_readiness([*events, *diagnostics])
    if args.output:
        JsonlSink(args.output, append=False).write_many(diagnostics)
    result = {
        "input_event_count": len(events),
        "diagnostic_event_count": len(diagnostics),
        "output": args.output,
        "decision_flow_readiness": readiness.to_dict(),
    }
    print(strict_json_dumps(result))
    if args.fail_on == "never":
        return 0
    if args.fail_on == "failed" and readiness.failed_diagnostic_count:
        return 1
    if args.fail_on == "warning" and (
        readiness.failed_diagnostic_count
        or readiness.warning_diagnostic_count
        or readiness.gaps
    ):
        return 1
    return 0


def _cmd_agentic_security_readiness(args: argparse.Namespace) -> int:
    events = read_jsonl_events(args.path)
    assert_valid_agent_tracker_events(events, allow_full_io=args.allow_full_io)
    print(strict_json_dumps(assess_agentic_security_readiness(events).to_dict()))
    return 0


def _cmd_proof_readiness(args: argparse.Namespace) -> int:
    events = read_jsonl_events(args.path)
    assert_valid_agent_tracker_events(events, allow_full_io=args.allow_full_io)
    print(strict_json_dumps(assess_proof_readiness(events).to_dict()))
    return 0


def _cmd_arena_readiness(args: argparse.Namespace) -> int:
    events = read_jsonl_events(args.path)
    assert_valid_agent_tracker_events(events, allow_full_io=args.allow_full_io)
    print(strict_json_dumps(assess_arena_readiness(events).to_dict()))
    return 0


def _cmd_repair_pack(args: argparse.Namespace) -> int:
    events = read_jsonl_events(args.path)
    assert_valid_agent_tracker_events(events, allow_full_io=args.allow_full_io)
    pack = build_repair_pack(events, max_findings=args.max_findings)
    if args.output:
        Path(args.output).write_text(strict_json_dumps(pack) + "\n", encoding="utf-8")
    if args.prompt_output:
        Path(args.prompt_output).write_text(pack["prompt"] + "\n", encoding="utf-8")
    if not args.output and not args.prompt_output:
        print(strict_json_dumps(pack))
    else:
        print(
            strict_json_dumps(
                {
                    "event_count": pack["event_count"],
                    "finding_count": len(pack["findings"]),
                    "output": args.output,
                    "prompt_output": args.prompt_output,
                }
            )
        )
    return 0


def _cmd_dataset_from_events(args: argparse.Namespace) -> int:
    events = read_jsonl_events(args.path)
    assert_valid_agent_tracker_events(events, allow_full_io=args.allow_full_io)
    items = build_dataset_items(events)
    output = Path(args.output)
    output.write_text(
        "".join(f"{strict_json_dumps(item)}\n" for item in items),
        encoding="utf-8",
    )
    print(strict_json_dumps({"dataset_item_count": len(items), "output": args.output}))
    return 0


def _cmd_eval_plan(args: argparse.Namespace) -> int:
    events = read_jsonl_events(args.path)
    assert_valid_agent_tracker_events(events, allow_full_io=args.allow_full_io)
    plan = build_eval_plan(events)
    if args.output:
        Path(args.output).write_text(strict_json_dumps(plan) + "\n", encoding="utf-8")
        print(strict_json_dumps({"output": args.output, **plan}))
    else:
        print(strict_json_dumps(plan))
    return 0


def _cmd_experiment_manifest(args: argparse.Namespace) -> int:
    raw_pack = strict_json_loads(
        Path(args.from_repair_pack).read_text(encoding="utf-8")
    )
    if not isinstance(raw_pack, dict):
        raise ValueError("repair pack must be a JSON object")
    manifest = build_experiment_manifest(raw_pack, changes=_parse_changes(args.change))
    if args.output:
        Path(args.output).write_text(
            strict_json_dumps(manifest) + "\n", encoding="utf-8"
        )
        print(strict_json_dumps({"output": args.output, **manifest}))
    else:
        print(strict_json_dumps(manifest))
    return 0


def _cmd_queue_health(args: argparse.Namespace) -> int:
    queue = LocalQueue(Path(args.queue_dir), max_queue_bytes=args.max_queue_bytes)
    print(
        strict_json_dumps(asdict(queue.health(max_batch_events=args.max_batch_events)))
    )
    return 0


def _cmd_flush(args: argparse.Namespace) -> int:
    client = AgentTracker.from_env(
        project=args.project,
        environment=args.environment,
        agent_id=args.agent_id,
    )
    if args.drain:
        summary = client.flush_all(
            max_batches=args.max_batches,
            dry_run=args.dry_run,
            raise_on_error=args.fail_fast,
        )
    else:
        summary = client.flush(
            dry_run=args.dry_run,
            raise_on_error=args.fail_fast,
        )
    print(strict_json_dumps(asdict(summary)))
    return 0


def _cmd_doctor_upload(args: argparse.Namespace) -> int:
    return _cmd_diagnostic_upload(
        args,
        run_type="agent_tracker_doctor_upload",
        diagnostic_kind="doctor_upload",
    )


def _cmd_canary(args: argparse.Namespace) -> int:
    return _cmd_diagnostic_upload(
        args,
        run_type="agent_tracker_ingestion_canary",
        diagnostic_kind="canary",
    )


def _cmd_diagnostic_upload(
    args: argparse.Namespace,
    *,
    run_type: str,
    diagnostic_kind: str,
) -> int:
    base_config = Config.from_env(
        project=args.project,
        environment=args.environment,
        agent_id=args.agent_id,
    )
    with TemporaryDirectory(prefix=f"agent-tracker-{diagnostic_kind}-") as tmp:
        client = AgentTracker(
            replace(base_config, queue_dir=Path(tmp), agent_id=base_config.agent_id)
        )
        with client.run(
            run_type=run_type,
            trigger="cli",
            metadata={
                "diagnostic": True,
                "diagnostic_kind": diagnostic_kind,
                "sdk_version": SDK_VERSION,
            },
        ) as run:
            run.cost_usage(
                provider="agent-tracker",
                usage_kind=diagnostic_kind,
                quantity=1,
                component="upload",
                severity="info",
            )
        summary = client.flush(
            dry_run=not args.live,
            raise_on_error=args.fail_fast,
        )
    result = {
        "endpoint": f"{base_config.endpoint}/v1/events/batch",
        "project": base_config.project,
        "agent_id": base_config.agent_id,
        "environment": base_config.environment,
        "diagnostic_kind": diagnostic_kind,
        "gzip": base_config.gzip_enabled,
        "live": args.live,
        "summary": asdict(summary),
    }
    print(strict_json_dumps(result))
    return 0


def _cmd_emit_sample(args: argparse.Namespace) -> int:
    fixture_parts = ("schemas", "fixtures", "valid")
    if args.profile == "reporting":
        fixture_parts = ("schemas", "fixtures", "reporting")
    fixtures = [
        read_json_resource(*fixture_parts, name)
        for name in list_resource_names(*fixture_parts)
    ]
    if args.output == "-":
        for event in fixtures:
            print(strict_json_dumps(event))
        return 0
    sink = JsonlSink(args.output, append=False)
    sink.write_many(fixtures)
    print(strict_json_dumps({"event_count": len(fixtures), "output": args.output}))
    return 0


def _cmd_doctor_repo(args: argparse.Namespace) -> int:
    report = doctor_repo(args.path)
    if args.write_plan:
        Path(args.write_plan).write_text(format_doctor_plan(report), encoding="utf-8")
    if args.json:
        print(strict_json_dumps(report.to_dict()))
    else:
        print(format_doctor_report(report), end="")
    return 0


def _cmd_print_agent_prompt(args: argparse.Namespace) -> int:
    print(read_text_resource("prompts", _PROMPT_PROFILES[args.profile]), end="")
    return 0


def _cmd_export_aitrade(args: argparse.Namespace) -> int:
    if not args.rows_json and not args.database_url:
        raise ValueError("pass --rows-json or --database-url")
    exporter = AitradeExporter(
        project=args.project,
        agent_id=args.agent_id,
        environment=args.environment,
        database_url=args.database_url,
    )
    rows_by_table: dict[str, list[dict[str, Any]]] | None = None
    if args.rows_json:
        rows_by_table = _load_rows_json(args.rows_json)
    summary = exporter.export_jsonl(
        args.output,
        rows_by_table=rows_by_table,
        limit_per_table=args.limit_per_table,
        append=False,
    )
    print(strict_json_dumps(summary.to_dict()))
    return 0


def _cmd_map_events(args: argparse.Namespace) -> int:
    summary = export_mapped_events(args.config, args.output)
    print(strict_json_dumps(summary.to_dict()))
    return 0


def _load_rows_json(path: str) -> dict[str, list[dict[str, Any]]]:
    value = strict_json_loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("rows JSON must be an object keyed by table name")
    result: dict[str, list[dict[str, Any]]] = {}
    for table, rows in value.items():
        if not isinstance(table, str) or not isinstance(rows, list):
            raise ValueError("rows JSON must map table names to row arrays")
        result[table] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"{table} contains a non-object row")
            result[table].append(row)
    return result


def _parse_changes(changes: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw in changes:
        if "=" not in raw:
            raise ValueError("--change must use key=value")
        key, value = raw.split("=", maxsplit=1)
        key = key.strip()
        if not key:
            raise ValueError("--change key must be non-empty")
        parsed[key] = value.strip()
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
