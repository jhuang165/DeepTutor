from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

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
        "direct_answer_honored": True if case.direct_answer else None,
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


def _learning_decision(*, scope: str, help_level: int = 1, route: str = "chat"):
    from deeptutor.learning.coordinator.models import (
        ActivityKind,
        ActivityPlan,
        LearningDecision,
        LearningScope,
    )

    return LearningDecision(
        scope=LearningScope(scope),
        route=route,
        goal="Controlled evaluation goal",
        activity=ActivityPlan(
            kind=ActivityKind.EXPLANATION,
            objective="Observe the executed boundary",
            learner_action="Respond",
            help_level=help_level,
        ),
        reason="controlled test boundary",
        confidence=1.0,
        requires_approval=scope == "path",
    )


def _completed_events(response: str = "The direct answer is x = 5."):
    from deeptutor.core.stream import StreamEvent, StreamEventType

    return (
        StreamEvent(
            type=StreamEventType.RESULT,
            source="chat",
            metadata={"response": response, "metadata": {"cost_summary": {}}},
        ),
        StreamEvent(
            type=StreamEventType.DONE,
            source="chat",
            metadata={"status": "completed"},
        ),
    )


def _local_runner(module, tmp_path):
    return module.LocalDeepTutorRunner(
        runtime_root=tmp_path / "runtime",
        raw_output_dir=tmp_path / "raw",
        seed_supported=False,
    )


def _raw_events(tmp_path, result):
    return json.loads((tmp_path / "raw" / Path(result.raw_output_ref).name).read_text())["events"]


def _fixture():
    return {
        "model": "controlled-model",
        "settings": {"temperature": 0.0},
        "source_ids": [],
        "provider_seed": 317,
        "seed_supported": False,
    }


def _patch_llm_config(monkeypatch) -> None:
    from deeptutor.services.llm import config

    monkeypatch.setattr(
        config,
        "get_llm_config",
        lambda: SimpleNamespace(provider_name="controlled", model="controlled-model"),
    )


def _patch_orchestrator(monkeypatch, handle) -> None:
    from deeptutor.runtime import orchestrator

    class ControlledOrchestrator:
        def __init__(self, _registry) -> None:
            pass

    ControlledOrchestrator.handle = handle
    monkeypatch.setattr(orchestrator, "ChatOrchestrator", ControlledOrchestrator)


def test_local_runner_detects_path_created_during_coordinator_preparation(
    tmp_path, monkeypatch
) -> None:
    module = _load_eval_module()
    case = next(case for case in module.load_cases(CASES_PATH) if case.id == "math-path-007")
    from deeptutor.learning.coordinator import LearningCoordinator
    from deeptutor.learning.models import LearningProgress

    async def creating_prepare(self, payload, available_capabilities, llm_config):
        del payload, available_capabilities, llm_config
        self._store.save(LearningProgress(book_id="created-during-prepare"))
        return _learning_decision(scope="path")

    async def completed_handle(self, context):
        del self, context
        for event in _completed_events():
            yield event

    monkeypatch.setattr(LearningCoordinator, "prepare_payload", creating_prepare)
    _patch_orchestrator(monkeypatch, completed_handle)
    _patch_llm_config(monkeypatch)

    result = asyncio.run(_local_runner(module, tmp_path).run(case, "active", _fixture(), None))

    assert result.status == "completed", _raw_events(tmp_path, result)
    assert result.path_created_before_approval is True
    assert "path_created_before_approval" in module.score_contracts(case, result).failures


def test_local_runner_scores_direct_answer_from_terminal_output_not_planned_help(
    tmp_path, monkeypatch
) -> None:
    module = _load_eval_module()
    case = next(case for case in module.load_cases(CASES_PATH) if case.id == "math-direct-002")
    from deeptutor.learning.coordinator import LearningCoordinator

    async def direct_prepare(self, payload, available_capabilities, llm_config):
        del self, payload, available_capabilities, llm_config
        return _learning_decision(scope="answer", help_level=4)

    async def question_only_handle(self, context):
        del self, context
        for event in _completed_events("What value do you think x should have?"):
            yield event

    monkeypatch.setattr(LearningCoordinator, "prepare_payload", direct_prepare)
    _patch_orchestrator(monkeypatch, question_only_handle)
    _patch_llm_config(monkeypatch)

    result = asyncio.run(_local_runner(module, tmp_path).run(case, "active", _fixture(), None))

    assert result.status == "completed", _raw_events(tmp_path, result)
    assert result.final_help_level == 4
    assert result.direct_answer_honored is False
    assert "direct_answer_not_honored" in module.score_contracts(case, result).failures


def test_local_runner_rejects_guidance_then_answer_solicitation_as_a_direct_answer(
    tmp_path, monkeypatch
) -> None:
    module = _load_eval_module()
    case = next(case for case in module.load_cases(CASES_PATH) if case.id == "math-direct-002")
    from deeptutor.learning.coordinator import LearningCoordinator

    async def direct_prepare(self, payload, available_capabilities, llm_config):
        del self, payload, available_capabilities, llm_config
        return _learning_decision(scope="answer", help_level=4)

    async def guidance_only_handle(self, context):
        del self, context
        for event in _completed_events(
            "Let's work through it together. What value do you think x should have?"
        ):
            yield event

    monkeypatch.setattr(LearningCoordinator, "prepare_payload", direct_prepare)
    _patch_orchestrator(monkeypatch, guidance_only_handle)
    _patch_llm_config(monkeypatch)

    result = asyncio.run(_local_runner(module, tmp_path).run(case, "active", _fixture(), None))

    assert result.status == "completed", _raw_events(tmp_path, result)
    assert result.direct_answer_honored is False
    assert "direct_answer_not_honored" in module.score_contracts(case, result).failures


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        pytest.param(
            "Try one more step, and I'll guide you from there.",
            False,
            id="english-attempt-solicitation",
        ),
        pytest.param(
            "我们一起来分析。你觉得 x 应该是多少？",
            False,
            id="chinese-guidance-and-solicitation",
        ),
        pytest.param(
            "A useful next move is to inspect the evidence. Which source would you choose?",
            False,
            id="english-unlisted-guidance-and-solicitation",
        ),
        pytest.param(
            "下一步可以检查证据。你会选择哪个来源？",
            False,
            id="chinese-unlisted-guidance-and-solicitation",
        ),
        pytest.param(
            "Try setting x = 5. Does that satisfy the equation?",
            False,
            id="english-guidance-with-trial-equation",
        ),
        pytest.param(
            "试着令 x = 5。它满足方程吗？",
            False,
            id="chinese-guidance-with-trial-equation",
        ),
        pytest.param(
            "The direct answer is x = 5. Can you explain why?",
            True,
            id="english-answer-with-follow-up",
        ),
        pytest.param(
            "答案是 x = 5。你能解释原因吗？",
            True,
            id="chinese-answer-with-follow-up",
        ),
    ],
)
def test_local_runner_distinguishes_guidance_from_substantive_bilingual_answers(
    tmp_path, monkeypatch, response, expected
) -> None:
    module = _load_eval_module()
    case = next(case for case in module.load_cases(CASES_PATH) if case.id == "math-direct-002")
    from deeptutor.learning.coordinator import LearningCoordinator

    async def direct_prepare(self, payload, available_capabilities, llm_config):
        del self, payload, available_capabilities, llm_config
        return _learning_decision(scope="answer", help_level=4)

    async def controlled_handle(self, context):
        del self, context
        for event in _completed_events(response):
            yield event

    monkeypatch.setattr(LearningCoordinator, "prepare_payload", direct_prepare)
    _patch_orchestrator(monkeypatch, controlled_handle)
    _patch_llm_config(monkeypatch)

    result = asyncio.run(_local_runner(module, tmp_path).run(case, "active", _fixture(), None))

    assert result.status == "completed", _raw_events(tmp_path, result)
    assert result.direct_answer_honored is expected
    assert ("direct_answer_not_honored" in module.score_contracts(case, result).failures) is (
        not expected
    )


def test_local_runner_observes_actual_tool_route_availability(tmp_path, monkeypatch) -> None:
    module = _load_eval_module()
    case = next(
        case for case in module.load_cases(CASES_PATH) if case.id == "math-missing-tool-008"
    )
    from deeptutor.runtime.registry import tool_registry

    class AvailableToolRegistry:
        def get(self, name):
            return object() if name in case.missing_tools else None

    async def completed_handle(self, context):
        del self, context
        for event in _completed_events():
            yield event

    monkeypatch.setattr(tool_registry, "get_tool_registry", lambda: AvailableToolRegistry())
    _patch_orchestrator(monkeypatch, completed_handle)

    result = asyncio.run(_local_runner(module, tmp_path).run(case, "off", _fixture(), None))

    assert result.status == "completed", _raw_events(tmp_path, result)
    assert result.route == "chat"
    assert result.route_available is True
    assert "route_availability_mismatch" in module.score_contracts(case, result).failures


def test_local_runner_resumes_from_a_persisted_learning_thread(tmp_path, monkeypatch) -> None:
    module = _load_eval_module()
    case = next(
        case for case in module.load_cases(CASES_PATH) if case.id == "math-interrupted-009"
    )
    from deeptutor.learning import storage

    async def inspect_persisted_thread(self, context):
        del self
        store = storage.LearningStore()
        thread = store.get_learning_thread("eval-math-thread")
        assert thread is not None
        assert thread.session_id == context.session_id
        assert thread.next_activity["activity_id"] == "limits-transfer"
        for event in _completed_events():
            yield event

    _patch_orchestrator(monkeypatch, inspect_persisted_thread)

    result = asyncio.run(
        _local_runner(module, tmp_path).run(
            case,
            "off",
            _fixture(),
            dict(case.stored_turn_state),
        )
    )

    assert result.status == "completed", _raw_events(tmp_path, result)
    assert result.resumed_from_state == {
        "thread_id": "eval-math-thread",
        "next_activity_id": "limits-transfer",
        "help_level": 2,
    }


def test_local_runner_isolates_every_arm_and_case_store(tmp_path, monkeypatch) -> None:
    module = _load_eval_module()
    cases = module.load_cases(CASES_PATH)
    first = next(case for case in cases if case.id == "math-interrupted-009")
    second = next(case for case in cases if case.id == "science-answer-001")
    from deeptutor.learning import storage
    from deeptutor.learning.models import LearningProgress

    observed_initial_paths = []
    observed_initial_threads = []

    async def mutate_store(self, context):
        del self
        store = storage.LearningStore()
        observed_initial_paths.append(store.list_all())
        observed_initial_threads.append(
            [
                {
                    "thread_id": thread.thread_id,
                    "session_id": thread.session_id,
                    "scope": thread.scope,
                    "goal": thread.goal,
                    "status": thread.status.value,
                    "next_activity": thread.next_activity,
                }
                for thread in store.list_learning_threads()
            ]
        )
        store.save(LearningProgress(book_id=f"write-{context.session_id}"))
        for event in _completed_events():
            yield event

    _patch_orchestrator(monkeypatch, mutate_store)
    runner = _local_runner(module, tmp_path)

    raw_refs = []
    for case, mode in (
        (first, "off"),
        (first, "active"),
        (second, "off"),
        (first, "off"),
    ):
        if mode == "active":
            from deeptutor.learning.coordinator import LearningCoordinator

            async def prepare(self, payload, available_capabilities, llm_config):
                del self, payload, available_capabilities, llm_config
                return _learning_decision(scope="path")

            monkeypatch.setattr(LearningCoordinator, "prepare_payload", prepare)
            _patch_llm_config(monkeypatch)
        resume_state = dict(case.stored_turn_state) if case.interrupted else None
        result = asyncio.run(runner.run(case, mode, _fixture(), resume_state))
        assert result.status == "completed", _raw_events(tmp_path, result)
        raw_refs.append(result.raw_output_ref)

    assert observed_initial_paths == [[], [], [], []]
    assert observed_initial_threads[0] == observed_initial_threads[1]
    assert observed_initial_threads[0] == observed_initial_threads[3]
    assert observed_initial_threads[2] == []
    assert len(set(raw_refs)) == len(raw_refs)


@pytest.mark.parametrize(
    ("exception_factory", "expected_status"),
    [
        pytest.param(
            lambda: __import__(
                "deeptutor.services.llm.exceptions", fromlist=["LLMConfigError"]
            ).LLMConfigError("provider is not configured"),
            "blocked",
            id="explicit-provider-config-error-is-blocked",
        ),
        pytest.param(
            lambda: RuntimeError("model invariant violated in implementation"),
            "failed",
            id="implementation-error-containing-model-is-failed",
        ),
    ],
)
def test_local_runner_contains_preparation_errors_and_classifies_explicitly(
    tmp_path, monkeypatch, exception_factory, expected_status
) -> None:
    module = _load_eval_module()
    case = next(case for case in module.load_cases(CASES_PATH) if case.id == "math-answer-001")
    from deeptutor.learning.coordinator import LearningCoordinator

    async def failed_prepare(self, payload, available_capabilities, llm_config):
        del self, payload, available_capabilities, llm_config
        raise exception_factory()

    monkeypatch.setattr(LearningCoordinator, "prepare_payload", failed_prepare)
    _patch_llm_config(monkeypatch)

    result = asyncio.run(_local_runner(module, tmp_path).run(case, "active", _fixture(), None))

    assert result.status == expected_status
    if expected_status == "blocked":
        assert result.blocked_reason
    else:
        assert result.blocked_reason == ""


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
            {"direct_answer_honored": False},
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
                raw_output_ref=(
                    "raw/80ae3f4a.txt"
                    if coordinator_mode == "off"
                    else "raw/c13d902b.txt"
                ),
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
        "provider_seed_applied": True,
    }
    assert set(pair["blinded_outputs"]) == {"A", "B"}
    assert {item["raw_output_ref"] for item in pair["blinded_outputs"].values()} == {
        "raw/80ae3f4a.txt",
        "raw/c13d902b.txt",
    }
    for output in pair["blinded_outputs"].values():
        assert "mode" not in output
        assert output["latency_ms"] in {12.5, 14.0}
        assert output["token_usage"]["total_tokens"] == 15
    assert all(
        score is None
        for label_scores in pair["human_rubric"].values()
        for score in label_scores.values()
    )
    assert "latency_ms" not in pair
    assert "token_usage" not in pair
    assert '"provider_seed":' not in json.dumps(pair)


def test_blinded_mapping_is_cryptographically_random_and_raw_refs_do_not_identify_modes() -> None:
    module = _load_eval_module()
    rubric = module.load_rubric(RUBRIC_PATH)
    case = next(case for case in module.load_cases(CASES_PATH) if case.id == "math-answer-001")

    class RawWritingRunner:
        async def run(self, requested_case, coordinator_mode, fixture, resume_state):
            del requested_case, fixture, resume_state
            return module.EvalResult(
                status="completed",
                scope="answer",
                approval_requested=False,
                direct_answer_honored=None,
                route="chat",
                route_available=True,
                raw_output_ref=(
                    "raw/80ae3f4a.json" if coordinator_mode == "off" else "raw/c13d902b.json"
                ),
                latency_ms=1.0 if coordinator_mode == "off" else 2.0,
                token_usage={"total_tokens": 1 if coordinator_mode == "off" else 2},
            )

    labels_for_baseline = set()
    for _ in range(32):
        report = asyncio.run(
            module.run_paired_evaluation(
                [case],
                runner=RawWritingRunner(),
                rubric=rubric,
                model="test-model",
                settings={},
                provider_seed=317,
                seed_supported=True,
            )
        )
        outputs = report["cases"][0]["blinded_outputs"]
        labels_for_baseline.add(
            next(label for label, value in outputs.items() if value["latency_ms"] == 1.0)
        )
        assert all("off" not in value["raw_output_ref"] for value in outputs.values())
        assert all("active" not in value["raw_output_ref"] for value in outputs.values())

    assert labels_for_baseline == {"A", "B"}


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
                direct_answer_honored=coordinator_mode == "active",
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
    auth_token = "opaque-auth-token-value"
    id_token = "opaque-id-token-value"
    credential = "opaque-service-credential"
    report = {
        "api_key": secret,
        "auth_token": auth_token,
        "id-token": id_token,
        "service_credential": credential,
        "message": f"Authorization: Bearer {secret}",
        "provider_error": f"request failed, token: {opaque_secret}",
        "raw_output_ref": (
            "https://example.test/raw?id=1"
            f"&token={secret}&auth_token={auth_token}&id_token={id_token}"
        ),
    }

    module.write_redacted_report(output, report)

    serialized = output.read_text(encoding="utf-8")
    parsed = json.loads(serialized)
    assert secret not in serialized
    assert opaque_secret not in serialized
    assert auth_token not in serialized
    assert id_token not in serialized
    assert credential not in serialized
    assert parsed["api_key"] == "[REDACTED]"
    assert parsed["auth_token"] == "[REDACTED]"
    assert parsed["id-token"] == "[REDACTED]"
    assert parsed["service_credential"] == "[REDACTED]"
    assert "Bearer [REDACTED]" in parsed["message"]
    assert "token: [REDACTED]" in parsed["provider_error"]
    assert "token=%5BREDACTED%5D" in parsed["raw_output_ref"]
    assert "auth_token=%5BREDACTED%5D" in parsed["raw_output_ref"]
    assert "id_token=%5BREDACTED%5D" in parsed["raw_output_ref"]


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
