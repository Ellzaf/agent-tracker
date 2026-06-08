from __future__ import annotations

import json
import stat
from pathlib import Path

from agent_tracker import AgentTracker, Config, JsonlSink
from agent_tracker.cli import main
from agent_tracker.doctor import doctor_repo
from agent_tracker.integration import (
    AgentTrackerIntegrationReport,
    IntegrationSurface,
    SourceRef,
    emit_integration_report,
)
from agent_tracker.resources import read_json_resource
from agent_tracker.serialization import strict_json_dumps
from agent_tracker.sink import read_jsonl_events


def test_jsonl_sink_redacts_and_appends_events(tmp_path: Path) -> None:
    client = AgentTracker(
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
    assert "Integrate Ellzaf Agent Tracker" in output

    assert main(["print-agent-prompt", "--profile", "custom"]) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "Integrate Agent Tracker Into A Custom Python Trading Agent" in output

    path = tmp_path / "sample.jsonl"
    assert main(["emit-sample", "--profile", "ebook", "--output", str(path)]) == 0
    assert main(["validate-jsonl", str(path), "--strict-mistakes"]) == 0


def test_cli_doctor_upload_dry_run_reports_endpoint(
    tmp_path: Path,
    capsys: object,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ELLZAF_PROJECT", "paper-agent")
    monkeypatch.setenv("ELLZAF_API_KEY", "ellzaf_trk_testkey123456789")
    monkeypatch.setenv("ELLZAF_QUEUE_DIR", str(tmp_path / "queue"))

    assert main(["doctor-upload"]) == 0

    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output["endpoint"] == "https://ellzaf.com/v1/events/batch"
    assert output["summary"]["dry_run"] is True
    assert output["summary"]["reason_code"] == "dry_run"


def test_cli_flush_dry_run_does_not_move_queue_files(
    tmp_path: Path,
    capsys: object,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ELLZAF_PROJECT", "paper-agent")
    monkeypatch.setenv("ELLZAF_API_KEY", "ellzaf_trk_testkey123456789")
    monkeypatch.setenv("ELLZAF_QUEUE_DIR", str(tmp_path / "queue"))

    client = AgentTracker(
        Config(
            project="paper-agent",
            queue_dir=tmp_path / "queue",
            api_key="ellzaf_trk_testkey123456789",
        )
    )
    client.event("risk.check.completed", run_id="run_cli", payload={"approved": True})

    assert main(["flush", "--dry-run", "--drain"]) == 0

    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output["dry_run"] is True
    assert output["stop_reason"] == "dry_run"
    assert len(list((tmp_path / "queue" / "pending").glob("*.jsonl"))) == 1


def test_cli_emit_reporting_sample_validate_and_show_readiness(
    tmp_path: Path, capsys: object
) -> None:
    path = tmp_path / "reporting.jsonl"

    assert main(["emit-sample", "--profile", "reporting", "--output", str(path)]) == 0
    assert main(["validate-jsonl", str(path), "--profile", "strict-reporting"]) == 0
    validated = json.loads(capsys.readouterr().out.splitlines()[-1])  # type: ignore[attr-defined]
    assert validated["reporting_readiness"]["strict_reporting_ready"] is True
    assert validated["reporting_readiness"]["can_compute_flow_adjusted_pnl"] is True

    assert main(["reporting-readiness", str(path)]) == 0
    readiness = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert readiness["strict_reporting_ready"] is True
    assert readiness["missing_fields"] == []
    assert readiness["can_generate_repair_prompts"] is True

    assert main(["tier-readiness", str(path)]) == 0
    tier = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert tier["event_count"] > 0
    assert "free_ready" in tier

    assert main(["agentic-security-readiness", str(path)]) == 0
    security = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert "gaps" in security

    repair_output = tmp_path / "repair.json"
    prompt_output = tmp_path / "repair.md"
    assert (
        main(
            [
                "repair-pack",
                str(path),
                "--output",
                str(repair_output),
                "--prompt-output",
                str(prompt_output),
            ]
        )
        == 0
    )
    repair_summary = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert repair_summary["output"] == str(repair_output)
    assert "Preserve trading behavior" in prompt_output.read_text(encoding="utf-8")

    dataset_output = tmp_path / "dataset.jsonl"
    assert (
        main(["dataset-from-events", str(path), "--output", str(dataset_output)])
        == 0
    )
    dataset_summary = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert dataset_summary["dataset_item_count"] >= 1

    eval_output = tmp_path / "eval.json"
    assert main(["eval-plan", str(path), "--output", str(eval_output)]) == 0
    eval_summary = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert eval_summary["output"] == str(eval_output)


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


def test_cli_doctor_repo_writes_coding_agent_plan(tmp_path: Path) -> None:
    (tmp_path / "agent.py").write_text(
        "prompt_hash = 'sha256:x'\nrisk_checks = []\n",
        encoding="utf-8",
    )
    plan = tmp_path / "agent-tracker-plan.md"

    assert (
        main(["doctor-repo", "--path", str(tmp_path), "--write-plan", str(plan)])
        == 0
    )

    text = plan.read_text(encoding="utf-8")
    assert "Agent Tracker Integration Plan" in text
    assert "Preserve trading behavior" in text
    assert "Do not add broker execution" in text
    assert "Doctor JSON" in text


def test_emit_integration_report_uses_public_dataclasses(tmp_path: Path) -> None:
    client = AgentTracker(Config(project="paper-agent", queue_dir=tmp_path))
    report = AgentTrackerIntegrationReport(
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
