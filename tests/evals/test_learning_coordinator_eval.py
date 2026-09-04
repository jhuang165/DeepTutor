from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "evals" / "learning_coordinator" / "cases.jsonl"
RUBRIC_PATH = ROOT / "evals" / "learning_coordinator" / "rubric.yaml"
SCRIPT_PATH = ROOT / "scripts" / "eval_learning_coordinator.py"


def _load_eval_module():
    if not SCRIPT_PATH.is_file():
        pytest.fail(f"evaluation runner is missing: {SCRIPT_PATH}")
    spec = importlib.util.spec_from_file_location("eval_learning_coordinator", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scenario_matrix_covers_required_domains_scopes_and_states() -> None:
    module = _load_eval_module()
    cases = module.load_cases(CASES_PATH)
    module.validate_scenario_matrix(cases)

    required_domains = {
        "mathematics",
        "physical_science",
        "programming",
        "humanities",
        "open_analysis",
    }
    assert len(cases) >= 40
    assert {case.domain for case in cases} == required_domains
    assert {case.scope for case in cases} == {"answer", "lesson", "path"}
    for domain in required_domains:
        assert {case.scope for case in cases if case.domain == domain} == {
            "answer",
            "lesson",
            "path",
        }
    assert all(case.version >= 1 for case in cases)
    assert any(case.misconception for case in cases)
    assert any(case.stuck_signal for case in cases)
    assert any(case.direct_answer for case in cases)
    assert any(case.attached_only for case in cases)
    assert any(case.missing_tools for case in cases)
    assert any(case.interrupted for case in cases)
    assert all("scope" in case.expected for case in cases)
    assert all("preferred_answer" not in case.expected for case in cases)
    assert all("reference_answer" not in case.expected for case in cases)


def test_scenario_matrix_rejects_a_domain_missing_a_required_state() -> None:
    module = _load_eval_module()
    cases = module.load_cases(CASES_PATH)
    for case in cases:
        if case.domain == "mathematics":
            case.direct_answer = False

    with pytest.raises(ValueError, match="mathematics.*direct_answer"):
        module.validate_scenario_matrix(cases)


def test_invalid_assessment_mutating_mastery_is_a_hard_contract_failure() -> None:
    module = _load_eval_module()
    case = next(case for case in module.load_cases(CASES_PATH) if case.id == "math-lesson-004")
    result = module.EvalResult(
        status="completed",
        scope="lesson",
        approval_requested=False,
        final_help_level=1,
        source_ids=[],
        evidence={
            "objective_id": "objective-1",
            "activity_id": "activity-1",
            "learner_response_ref": "chat-message:1:user",
            "rubric": [{"id": "explain", "passed": True}],
            "outcome": "correct",
            "help_level": 1,
            "source_refs": [],
            "timestamp": 1.0,
            "confidence": 0.9,
            "independent": True,
        },
        route="chat",
        route_available=True,
        assessment_valid=False,
        mastery_before={"objective-1": "unassessed"},
        mastery_after={"objective-1": "mastered"},
    )

    score = module.score_contracts(case, result)

    assert score.passed is False
    assert "invalid_assessment_mutated_mastery" in score.failures


def _complete_result(module, case, **overrides):
    values = {
        "status": "completed",
        "scope": case.scope,
        "approval_requested": bool(case.expected["requires_approval"]),
        "final_help_level": case.expected.get("help_level", 1),
        "source_ids": list(case.expected.get("allowed_source_ids", [])),
        "evidence": None,
        "route": "chat",
        "route_available": bool(case.expected["route_available"]),
        "resumed_from_state": dict(case.expected.get("resume_state", {})),
        "assessment_valid": None,
        "mastery_before": {},
        "mastery_after": {},
    }
    values.update(overrides)
    return module.EvalResult(**values)


def test_contract_scoring_reports_every_required_deterministic_violation() -> None:
    module = _load_eval_module()
    cases = {case.id: case for case in module.load_cases(CASES_PATH)}
    valid_evidence = {
        "objective_id": "objective-1",
        "activity_id": "activity-1",
        "learner_response_ref": "chat-message:1:user",
        "rubric": [{"id": "explain", "passed": True}],
        "outcome": "correct",
        "help_level": 1,
        "source_refs": [],
        "timestamp": 1.0,
        "confidence": 0.9,
        "independent": True,
    }
    checks = [
        (
            cases["math-answer-001"],
            {"scope": "lesson"},
            "scope_mismatch",
        ),
        (
            cases["math-path-007"],
            {"approval_requested": False},
            "approval_gate_mismatch",
        ),
        (
            cases["math-path-007"],
            {"path_created_before_approval": True},
            "path_created_before_approval",
        ),
        (
            cases["math-direct-002"],
            {"final_help_level": 3},
            "direct_answer_not_honored",
        ),
        (
            cases["math-attached-003"],
            {"source_ids": ["invented-web-source"]},
            "untrusted_source_ids",
        ),
        (
            cases["math-lesson-004"],
            {"evidence": {**valid_evidence, "learner_response_ref": ""}},
            "invalid_evidence_schema",
        ),
        (
            cases["math-stuck-006"],
            {"final_help_level": 0},
            "help_level_too_low",
        ),
        (
            cases["math-missing-tool-008"],
            {"route_available": True},
            "route_availability_mismatch",
        ),
        (
            cases["math-interrupted-009"],
            {"resumed_from_state": {"thread_id": "wrong"}},
            "resume_state_mismatch",
        ),
    ]

    for case, overrides, expected_failure in checks:
        score = module.score_contracts(case, _complete_result(module, case, **overrides))
        assert expected_failure in score.failures
        assert score.passed is False


def test_completed_contract_can_pass_but_provider_block_never_passes() -> None:
    module = _load_eval_module()
    case = next(case for case in module.load_cases(CASES_PATH) if case.id == "math-answer-001")

    completed = module.score_contracts(case, _complete_result(module, case))
    blocked = module.score_contracts(
        case,
        _complete_result(
            module,
            case,
            status="blocked",
            blocked_reason="provider_unavailable",
        ),
    )

    assert completed.passed is True
    assert completed.failures == []
    assert blocked.passed is False
    assert blocked.failures == ["provider_unavailable"]


def test_human_rubric_has_complete_anchored_blinded_human_only_gate() -> None:
    module = _load_eval_module()

    rubric = module.load_rubric(RUBRIC_PATH)

    assert set(rubric["dimensions"]) == {
        "factual_correctness",
        "method_fit",
        "diagnosis_quality",
        "source_honesty",
        "cognitive_load",
        "independent_transfer",
    }
    for anchors in rubric["dimensions"].values():
        assert set(anchors) == {0, 1, 2, 3, 4}
        assert all(isinstance(anchor, str) and anchor.strip() for anchor in anchors.values())
    assert rubric["review"]["labels"] == ["A", "B"]
    assert rubric["review"]["randomize_per_case"] is True
    assert rubric["review"]["initial_value"] is None
    assert rubric["release_gate"]["human_review_required"] is True
    assert rubric["release_gate"]["model_grader_may_certify"] is False


def test_paired_report_reuses_fixture_resumes_state_and_stays_human_blinded() -> None:
    module = _load_eval_module()
    rubric = module.load_rubric(RUBRIC_PATH)
    case = next(case for case in module.load_cases(CASES_PATH) if case.id == "math-interrupted-009")

    class ContractCheckingRunner:
        def __init__(self) -> None:
            self.first_fixture = None

        async def run(self, requested_case, coordinator_mode, fixture, resume_state):
            assert requested_case is case
            assert coordinator_mode in {"off", "active"}
            if self.first_fixture is None:
                self.first_fixture = fixture
            else:
                assert fixture is self.first_fixture
            assert resume_state == {
                "thread_id": "eval-math-thread",
                "next_activity_id": "limits-transfer",
                "help_level": 2,
            }
            return module.EvalResult(
                status="completed",
                scope="path",
                approval_requested=True,
                final_help_level=2,
                source_ids=[],
                route="chat",
                route_available=True,
                resumed_from_state={
                    "thread_id": "eval-math-thread",
                    "next_activity_id": "limits-transfer",
                },
                raw_output_ref=f"raw/{requested_case.id}-{coordinator_mode}.txt",
                latency_ms=12.5 if coordinator_mode == "off" else 14.0,
                token_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )

    report = asyncio.run(
        module.run_paired_evaluation(
            [case],
            runner=ContractCheckingRunner(),
            rubric=rubric,
            model="test-model",
            settings={"temperature": 0.2, "tools": []},
            provider_seed=317,
            seed_supported=True,
        )
    )

    assert report["schema_version"] == 1
    assert report["mode"] == "paired"
    assert report["release_gate"] == "awaiting_human_review"
    assert len(report["cases"]) == 1
    pair = report["cases"][0]
    assert pair["case_id"] == "math-interrupted-009"
    assert pair["domain"] == "mathematics"
    assert pair["scope"] == "path"
    assert pair["baseline_result"]["status"] == "completed"
    assert pair["coordinator_result"]["status"] == "completed"
    assert pair["contract_failures"] == {"baseline": [], "coordinator": []}
    assert pair["execution_fixture"] == {
        "model": "test-model",
        "settings": {"temperature": 0.2, "tools": []},
        "source_ids": [],
        "provider_seed": 317,
        "seed_supported": True,
    }
    assert set(pair["blinded_outputs"]) == {"A", "B"}
    assert {item["raw_output_ref"] for item in pair["blinded_outputs"].values()} == {
        "raw/math-interrupted-009-off.txt",
        "raw/math-interrupted-009-active.txt",
    }
    assert "mode" not in pair["blinded_outputs"]["A"]
    assert "mode" not in pair["blinded_outputs"]["B"]
    assert all(
        score is None
        for label_scores in pair["human_rubric"].values()
        for score in label_scores.values()
    )
    assert pair["latency_ms"] == {"baseline": 12.5, "coordinator": 14.0}
    assert pair["token_usage"]["baseline"]["total_tokens"] == 15


def test_baseline_contract_miss_is_reported_without_vetoing_coordinator_gate() -> None:
    module = _load_eval_module()
    rubric = module.load_rubric(RUBRIC_PATH)
    case = next(case for case in module.load_cases(CASES_PATH) if case.id == "math-direct-002")

    class ComparativeRunner:
        async def run(self, requested_case, coordinator_mode, fixture, resume_state):
            del requested_case, fixture, resume_state
            return module.EvalResult(
                status="completed",
                scope="answer",
                approval_requested=False,
                final_help_level=0 if coordinator_mode == "off" else 4,
                source_ids=[],
                route="chat",
                route_available=True,
            )

    report = asyncio.run(
        module.run_paired_evaluation(
            [case],
            runner=ComparativeRunner(),
            rubric=rubric,
            model="test-model",
            settings={"temperature": 0.2},
            provider_seed=317,
            seed_supported=True,
        )
    )

    assert report["cases"][0]["contract_failures"] == {
        "baseline": ["direct_answer_not_honored"],
        "coordinator": [],
    }
    assert report["release_gate"] == "awaiting_human_review"


def test_redacted_report_writer_removes_credentials_and_secret_query_values(tmp_path) -> None:
    module = _load_eval_module()
    output = tmp_path / "report.json"
    secret = "sk-secret-value-123456"
    opaque_secret = "opaquecredentialvalue0123456789abcdef"
    report = {
        "api_key": secret,
        "message": f"Authorization: Bearer {secret}",
        "provider_error": f"request failed, token: {opaque_secret}",
        "raw_output_ref": f"https://example.test/raw?id=1&token={secret}",
    }

    module.write_redacted_report(output, report)

    serialized = output.read_text(encoding="utf-8")
    parsed = json.loads(serialized)
    assert secret not in serialized
    assert opaque_secret not in serialized
    assert parsed["api_key"] == "[REDACTED]"
    assert "Bearer [REDACTED]" in parsed["message"]
    assert "token: [REDACTED]" in parsed["provider_error"]
    assert "token=%5BREDACTED%5D" in parsed["raw_output_ref"]


def test_unavailable_provider_blocks_every_case_with_populated_contract_fields() -> None:
    module = _load_eval_module()
    cases = module.load_cases(CASES_PATH)
    runner = module.UnavailableProviderRunner("No active LLM model is configured")

    async def run_all():
        results = []
        for case in cases:
            fixture = {
                "model": "",
                "settings": {},
                "source_ids": [source["id"] for source in case.sources],
                "provider_seed": 317,
                "seed_supported": False,
            }
            result = await runner.run(
                case,
                "active",
                fixture,
                dict(case.stored_turn_state) if case.interrupted else None,
            )
            results.append((case, result, module.score_contracts(case, result)))
        return results

    results = asyncio.run(run_all())

    assert len(results) == len(cases)
    for case, result, score in results:
        assert result.status == "blocked"
        assert result.scope == case.expected["scope"]
        assert result.approval_requested is bool(case.expected["requires_approval"])
        assert result.route_available is bool(case.expected["route_available"])
        assert result.token_usage == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        assert score.passed is False
        assert score.failures == ["provider_unavailable"]


def test_cli_offline_paired_run_writes_blocked_nonpassing_report(tmp_path) -> None:
    output = tmp_path / "paired.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--mode",
            "paired",
            "--output",
            str(output),
            "--offline",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["release_gate"] == "blocked_provider"
    assert len(report["cases"]) >= 40
    for pair in report["cases"]:
        assert pair["baseline_result"]["status"] == "blocked"
        assert pair["coordinator_result"]["status"] == "blocked"
        assert pair["baseline_result"]["deterministic_passed"] is False
        assert pair["coordinator_result"]["deterministic_passed"] is False
        assert pair["contract_failures"] == {
            "baseline": ["provider_unavailable"],
            "coordinator": ["provider_unavailable"],
        }
