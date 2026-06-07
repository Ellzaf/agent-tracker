from __future__ import annotations

import json
import stat
from pathlib import Path

from ellzaf_agent import Config, Ellzaf, JsonlSink
from ellzaf_agent.cli import main
from ellzaf_agent.doctor import doctor_repo
from ellzaf_agent.integration import (
    EllzafIntegrationReport,
    IntegrationSurface,
    SourceRef,
    emit_integration_report,
)
from ellzaf_agent.resources import read_json_resource
from ellzaf_agent.serialization import strict_json_dumps
from ellzaf_agent.sink import read_jsonl_events


def test_jsonl_sink_redacts_and_appends_events(tmp_path: Path) -> None:
    client = Ellzaf(
        Config(project="paper-agent", queue_dir=None, telemetry_enabled=False)
    )
    raw = client.event(
        "llm.call.started",
        run_id="run_sink",
        payload={"provider": "openai", "model": "example", "prompt": "hello"},
    )

    output = tmp_path / "events.jsonl"
    written = JsonlSink(output).write(raw)
    events = read_jsonl_events(output)

    assert len(events) == 1
    assert events[0]["event_id"] == written["event_id"]
    assert events[0]["payload"]["prompt"]["redacted"] is True


def test_jsonl_sink_truncates_new_files_with_private_permissions(
    tmp_path: Path,
) -> None:
    output = tmp_path / "events.jsonl"

    JsonlSink(output, append=False)

    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_cli_print_prompt_emit_sample_and_validate(
    tmp_path: Path, capsys: object
) -> None:
    assert main(["print-agent-prompt", "--profile", "ebook"]) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "Integrate Ellzaf Agent" in output

    path = tmp_path / "sample.jsonl"
    assert main(["emit-sample", "--profile", "ebook", "--output", str(path)]) == 0
    assert main(["validate-jsonl", str(path), "--strict-mistakes"]) == 0


def test_cli_emit_reporting_sample_validate_and_show_readiness(
    tmp_path: Path, capsys: object
) -> None:
    path = tmp_path / "reporting.jsonl"

    assert main(["emit-sample", "--profile", "reporting", "--output", str(path)]) == 0
    assert main(["validate-jsonl", str(path), "--profile", "strict-reporting"]) == 0
    validated = json.loads(capsys.readouterr().out.splitlines()[-1])  # type: ignore[attr-defined]
    assert validated["reporting_readiness"]["can_compute_flow_adjusted_pnl"] is True

    assert main(["reporting-readiness", str(path)]) == 0
    readiness = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert readiness["missing_fields"] == []
    assert readiness["can_generate_repair_prompts"] is True


def test_cli_validate_jsonl_rejects_raw_prompt(tmp_path: Path) -> None:
    event = read_json_resource("schemas", "fixtures", "invalid", "raw-prompt-leak.json")
    path = tmp_path / "invalid.jsonl"
    path.write_text(f"{strict_json_dumps(event)}\n", encoding="utf-8")

    assert main(["validate-jsonl", str(path)]) == 2


def test_doctor_repo_finds_ebook_style_surfaces(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("risk gates stay deterministic\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='toy'\n")
    src = tmp_path / "src"
    src.mkdir()
    (src / "agent.py").write_text(
        "\n".join(
            [
                "prompt_hash = 'sha256:x'",
                "llm_runs = []",
                "source_quality_repair = True",
                "market_tape_snapshots = []",
                "portfolio_targets = []",
                "risk_checks = []",
                "order_intents = []",
                "trade_journal = []",
                "portfolio_snapshots = []",
                "decision_replay = []",
                "def redact(value): return value",
            ]
        ),
        encoding="utf-8",
    )

    report = doctor_repo(tmp_path)

    coverage = {surface.name: surface.coverage for surface in report.surfaces}
    assert coverage["llm_calls"] == "implemented"
    assert coverage["source_collection"] == "implemented"
    assert coverage["risk_gate"] == "implemented"
    assert coverage["redaction"] == "implemented"


def test_emit_integration_report_uses_public_dataclasses(tmp_path: Path) -> None:
    client = Ellzaf(Config(project="paper-agent", queue_dir=tmp_path))
    report = EllzafIntegrationReport(
        project="paper-agent",
        surfaces=(
            IntegrationSurface(
                name="risk_checks",
                event_type="risk.check.completed",
                coverage="implemented",
                source_refs=(SourceRef(table="risk_checks", id="risk_1"),),
            ),
        ),
    )

    event = emit_integration_report(client, report, run_id="run_integration")

    assert event["event_type"] == "agent.run.completed"
    assert event["payload"]["coverage_status"] == "implemented"
