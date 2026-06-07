"""Command line interface for Ellzaf Agent."""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ellzaf_agent.adapters.aitrade import AitradeExporter
from ellzaf_agent.client import Ellzaf
from ellzaf_agent.constants import DEFAULT_MAX_QUEUE_BYTES, DEFAULT_QUEUE_DIR
from ellzaf_agent.doctor import doctor_repo, format_doctor_report
from ellzaf_agent.errors import EllzafError
from ellzaf_agent.queue import LocalQueue
from ellzaf_agent.resources import (
    list_resource_names,
    read_json_resource,
    read_text_resource,
)
from ellzaf_agent.serialization import strict_json_dumps, strict_json_loads
from ellzaf_agent.sink import JsonlSink, read_jsonl_events
from ellzaf_agent.testing import assert_valid_ellzaf_events

_PROMPT_PROFILES = {
    "ebook": "integrate-ebook-agent.md",
    "aitrade": "add-aitrade-adapter.md",
    "review": "review-ellzaf-integration.md",
    "backend": "backend-contract-check.md",
}


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (EllzafError, OSError, AssertionError, ValueError) as exc:
        print(f"ellzaf-agent: {exc}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ellzaf-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="write .env.ellzaf.example")
    init_parser.add_argument("--path", default=".env.ellzaf.example")
    init_parser.add_argument("--force", action="store_true")
    init_parser.set_defaults(func=_cmd_init)

    validate = subparsers.add_parser("validate-jsonl", help="validate a JSONL export")
    validate.add_argument("path")
    validate.add_argument("--profile", choices=["ebook", "aitrade"])
    validate.add_argument("--allow-full-io", action="store_true")
    validate.add_argument("--strict-mistakes", action="store_true")
    validate.set_defaults(func=_cmd_validate_jsonl)

    queue = subparsers.add_parser("queue-health", help="show local queue health")
    queue.add_argument("--queue-dir", default=DEFAULT_QUEUE_DIR)
    queue.add_argument("--max-queue-bytes", type=int, default=DEFAULT_MAX_QUEUE_BYTES)
    queue.set_defaults(func=_cmd_queue_health)

    flush = subparsers.add_parser("flush", help="flush the configured local queue")
    flush.add_argument("--project")
    flush.add_argument("--environment")
    flush.add_argument("--agent-id")
    flush.set_defaults(func=_cmd_flush)

    sample = subparsers.add_parser("emit-sample", help="emit bundled sample events")
    sample.add_argument("--profile", choices=["ebook"], default="ebook")
    sample.add_argument("--output", default="-")
    sample.set_defaults(func=_cmd_emit_sample)

    doctor = subparsers.add_parser("doctor-repo", help="inspect a repo for coverage")
    doctor.add_argument("--path", default=".")
    doctor.add_argument("--json", action="store_true")
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

    return parser


def _cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        raise ValueError(f"{path} already exists; pass --force to replace it")
    path.write_text(
        "\n".join(
            [
                'ELLZAF_PROJECT="my-paper-agent"',
                'ELLZAF_API_KEY=""',
                'ELLZAF_ENVIRONMENT="paper"',
                'ELLZAF_AGENT_ID="local-agent"',
                'ELLZAF_QUEUE_DIR=".ellzaf/queue"',
                'ELLZAF_TELEMETRY_ENABLED="true"',
                'ELLZAF_STORE_FULL_IO="false"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(path)
    return 0


def _cmd_validate_jsonl(args: argparse.Namespace) -> int:
    events = read_jsonl_events(args.path)
    assert_valid_ellzaf_events(
        events,
        profile=args.profile,
        allow_full_io=args.allow_full_io,
        require_mistake_family_for_mistakes=args.strict_mistakes,
    )
    print(strict_json_dumps({"valid": True, "event_count": len(events)}))
    return 0


def _cmd_queue_health(args: argparse.Namespace) -> int:
    queue = LocalQueue(Path(args.queue_dir), max_queue_bytes=args.max_queue_bytes)
    print(strict_json_dumps(asdict(queue.health())))
    return 0


def _cmd_flush(args: argparse.Namespace) -> int:
    client = Ellzaf.from_env(
        project=args.project,
        environment=args.environment,
        agent_id=args.agent_id,
    )
    print(strict_json_dumps(asdict(client.flush())))
    return 0


def _cmd_emit_sample(args: argparse.Namespace) -> int:
    fixtures = [
        read_json_resource("schemas", "fixtures", "valid", name)
        for name in list_resource_names("schemas", "fixtures", "valid")
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


if __name__ == "__main__":
    raise SystemExit(main())
