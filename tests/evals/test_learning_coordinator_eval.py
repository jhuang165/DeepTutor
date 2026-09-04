from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import stat
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


def _learning_decision(
    *,
    scope: str,
    help_level: int = 1,
    route: str = "chat",
    goal: str = "Controlled evaluation goal",
):
    from deeptutor.learning.coordinator.models import (
        ActivityKind,
        ActivityPlan,
        LearningDecision,
        LearningScope,
    )

    return LearningDecision(
        scope=LearningScope(scope),
        route=route,
        goal=goal,
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
        def __init__(self, _registry=None, **_kwargs) -> None:
            del _registry

    ControlledOrchestrator.handle = handle
    monkeypatch.setattr(orchestrator, "ChatOrchestrator", ControlledOrchestrator)
    _patch_llm_config(monkeypatch)


def _patch_runtime_support(monkeypatch) -> None:
    class ControlledContextBuilder:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def build(self, **_kwargs):
            return SimpleNamespace(
                conversation_history=[],
                conversation_summary="",
                context_text="",
                token_count=0,
                budget=0,
            )

    async def noop_async(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "deeptutor.services.session.context_builder.ContextBuilder",
        ControlledContextBuilder,
    )
    monkeypatch.setattr(
        "deeptutor.services.memory.get_memory_store",
        lambda: SimpleNamespace(read_l3_concat=lambda: "", emit=noop_async),
    )
    monkeypatch.setattr(
        "deeptutor.services.skill.get_skill_service",
        lambda: SimpleNamespace(
            summary_entries=lambda: [],
            load_always_for_context=lambda: "",
            load_for_context=lambda _skills: "",
            list_skills=lambda: [],
        ),
    )
    monkeypatch.setattr(
        "deeptutor.services.persona.get_persona_service",
        lambda: SimpleNamespace(load_for_context=lambda _name: ""),
    )


def test_local_runner_detects_path_created_during_coordinator_preparation(
    tmp_path, monkeypatch
) -> None:
    module = _load_eval_module()
    case = next(case for case in module.load_cases(CASES_PATH) if case.id == "math-path-007")
    from deeptutor.learning.coordinator import LearningCoordinator
    from deeptutor.learning.models import LearningProgress

    async def creating_prepare(self, payload, available_capabilities, llm_config):
        del self, payload, available_capabilities, llm_config
        from deeptutor.learning import storage

        storage.LearningStore().save(LearningProgress(book_id="created-during-prepare"))
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
    "response",
    [
        "You could try x = 5. Does that satisfy the equation?",
        "Maybe x = 5. Does that satisfy the equation?",
    ],
)
def test_local_runner_rejects_reviewer_hedged_equations_before_a_question(
    tmp_path, monkeypatch, response
) -> None:
    module = _load_eval_module()
    case = next(case for case in module.load_cases(CASES_PATH) if case.id == "math-direct-002")
    from deeptutor.learning.coordinator import LearningCoordinator

    async def direct_prepare(self, payload, available_capabilities, llm_config):
        del self, payload, available_capabilities, llm_config
        return _learning_decision(scope="answer", help_level=4)

    async def hedged_answer_handle(self, context):
        del self, context
        for event in _completed_events(response):
            yield event

    monkeypatch.setattr(LearningCoordinator, "prepare_payload", direct_prepare)
    _patch_orchestrator(monkeypatch, hedged_answer_handle)
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
            "First, set x = 5.",
            False,
            id="english-process-instruction-with-equation",
        ),
        pytest.param(
            "试着令 x = 5。它满足方程吗？",
            False,
            id="chinese-guidance-with-trial-equation",
        ),
        pytest.param(
            "首先，令 x = 5。",
            False,
            id="chinese-process-instruction-with-equation",
        ),
        pytest.param(
            "The direct answer is x = 5. Can you explain why?",
            False,
            id="english-answer-with-learner-question",
        ),
        pytest.param(
            "答案是 x = 5。你能解释原因吗？",
            False,
            id="chinese-answer-with-learner-question",
        ),
        pytest.param(
            "This response outlines a process for finding the answer.",
            False,
            id="english-generic-process-declarative",
        ),
        pytest.param(
            "下面说明求解过程。",
            False,
            id="chinese-generic-process-declarative",
        ),
        pytest.param(
            "Maybe x = 5.",
            False,
            id="english-hedged-equation-without-question",
        ),
        pytest.param(
            "也许 x = 5。",
            False,
            id="chinese-hedged-equation-without-question",
        ),
        pytest.param(
            "The direct answer is x = 5.",
            True,
            id="english-unhedged-direct-answer",
        ),
        pytest.param(
            "答案是 x = 5。",
            True,
            id="chinese-unhedged-direct-answer",
        ),
        pytest.param(
            "x = 5.",
            True,
            id="unhedged-substantive-equation",
        ),
        pytest.param(
            r"  \[x = 5\].  ",
            True,
            id="math-delimited-bare-equation",
        ),
        pytest.param(
            "42.",
            True,
            id="bare-numeric-value",
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
        from deeptutor.learning.coordinator.models import ActivityPlan

        store = storage.LearningStore()
        thread = store.get_learning_thread("eval-math-thread")
        assert thread is not None
        assert thread.session_id == context.session_id
        activity = ActivityPlan.model_validate(thread.next_activity)
        assert activity.kind.value == "explanation"
        assert activity.help_level == 2
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
    assert {
        key: result.resumed_from_state[key]
        for key in ("thread_id", "next_activity_id", "help_level")
    } == {
        "thread_id": "eval-math-thread",
        "next_activity_id": "limits-transfer",
        "help_level": 2,
    }
    assert result.resumed_from_state["next_activity"]["objective"] == case.prompt


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

    async def failed_handle(self, context):
        del self, context
        from deeptutor.core.stream import StreamEvent, StreamEventType

        metadata = (
            {"error_code": "provider_unavailable"}
            if expected_status == "blocked"
            else {}
        )
        yield StreamEvent(
            type=StreamEventType.ERROR,
            source="chat",
            content=str(exception_factory()),
            metadata=metadata,
        )
        yield StreamEvent(
            type=StreamEventType.DONE,
            source="chat",
            metadata={"status": "failed"},
        )

    monkeypatch.setattr(LearningCoordinator, "prepare_payload", failed_prepare)
    _patch_orchestrator(monkeypatch, failed_handle)
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
                review_material=(
                    "Ordinary response" if coordinator_mode == "off" else "Teaching response"
                ),
                latency_ms=12.5 if coordinator_mode == "off" else 14.0,
                token_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                decision_persisted=coordinator_mode == "active",
                thread_state=(
                    {"thread_id": "eval-math-thread"}
                    if coordinator_mode == "active"
                    else {}
                ),
                activity_consumed=coordinator_mode == "active",
            )

    artifacts = asyncio.run(
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

    reviewer = artifacts["reviewer"]
    machine = artifacts["machine"]
    assert reviewer["schema_version"] == 2
    assert "mode" not in reviewer
    assert "release_gate" not in reviewer
    assert machine["mode"] == "paired"
    assert machine["release_gate"] == "awaiting_human_review"
    assert len(reviewer["cases"]) == len(machine["cases"]) == 1
    review_pair = reviewer["cases"][0]
    machine_pair = machine["cases"][0]
    assert review_pair["case_id"] == "math-interrupted-009"
    assert review_pair["domain"] == "mathematics"
    assert review_pair["scope"] == "path"
    assert machine_pair["results"]["baseline"]["status"] == "completed"
    assert machine_pair["results"]["coordinator"]["status"] == "completed"
    assert machine_pair["contract_failures"] == {"baseline": [], "coordinator": []}
    assert machine_pair["execution_fixture"] == {
        "model": "test-model",
        "settings": {"temperature": 0.2, "tools": []},
        "source_ids": [],
        "provider_seed": 317,
        "provider_seed_applied": True,
    }
    assert set(review_pair["blinded_outputs"]) == {"A", "B"}
    assert {item["content"] for item in review_pair["blinded_outputs"].values()} == {
        "Ordinary response",
        "Teaching response",
    }
    for output in review_pair["blinded_outputs"].values():
        assert set(output) == {"content"}
    assert all(
        score is None
        for label_scores in review_pair["human_rubric"].values()
        for score in label_scores.values()
    )
    serialized_reviewer = json.dumps(reviewer)
    assert "baseline" not in serialized_reviewer
    assert "coordinator" not in serialized_reviewer
    assert "provider_seed" not in serialized_reviewer


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
                review_material=(
                    "First opaque response"
                    if coordinator_mode == "off"
                    else "Second opaque response"
                ),
                latency_ms=1.0 if coordinator_mode == "off" else 2.0,
                token_usage={"total_tokens": 1 if coordinator_mode == "off" else 2},
            )

    labels_for_baseline = set()
    for _ in range(32):
        artifacts = asyncio.run(
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
        outputs = artifacts["reviewer"]["cases"][0]["blinded_outputs"]
        mapping = artifacts["machine"]["cases"][0]["label_mapping"]
        labels_for_baseline.add(next(label for label, mode in mapping.items() if mode == "baseline"))
        assert all(set(value) == {"content"} for value in outputs.values())

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
                decision_persisted=coordinator_mode == "active",
            )

    artifacts = asyncio.run(
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

    machine = artifacts["machine"]
    assert machine["cases"][0]["contract_failures"] == {
        "baseline": ["direct_answer_not_honored"],
        "coordinator": [],
    }
    assert machine["release_gate"] == "awaiting_human_review"


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
                (
                    dict(case.stored_turn_state)
                    if case.interrupted or case.delayed_recall
                    else None
                ),
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
    reviewer = json.loads(output.read_text(encoding="utf-8"))
    machine_path = tmp_path / "paired.machine.json"
    machine = json.loads(machine_path.read_text(encoding="utf-8"))
    assert "release_gate" not in reviewer
    assert "baseline" not in json.dumps(reviewer)
    assert "coordinator" not in json.dumps(reviewer)
    assert machine["release_gate"] == "blocked_provider"
    assert len(reviewer["cases"]) == len(machine["cases"]) >= 40
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(machine_path.stat().st_mode) == 0o600
    for pair in machine["cases"]:
        assert pair["results"]["baseline"]["status"] == "blocked"
        assert pair["results"]["coordinator"]["status"] == "blocked"
        assert pair["results"]["baseline"]["deterministic_passed"] is False
        assert pair["results"]["coordinator"]["deterministic_passed"] is False
        assert pair["contract_failures"] == {
            "baseline": ["provider_unavailable"],
            "coordinator": ["provider_unavailable"],
        }


def test_scenario_matrix_requires_real_delayed_recall_follow_up_cases() -> None:
    # Production break caught: a matrix can satisfy every old state flag while
    # never running a later recall request against server-owned prior activity.
    module = _load_eval_module()
    cases = module.load_cases(CASES_PATH)
    delayed = [case for case in cases if case.delayed_recall]

    assert len(delayed) >= 5
    assert {case.domain for case in delayed} == module.REQUIRED_DOMAINS
    for case in delayed:
        assert case.follow_up_prompt.strip()
        assert case.stored_turn_state["thread_id"]
        activity = case.stored_turn_state["next_activity"]
        assert activity["assessment_method"] == "delayed_retrieval"
        assert activity["independent_required"] is True


def test_level_four_evidence_can_never_certify_independence() -> None:
    # Production break caught: a structurally valid evidence summary can mark a
    # complete-answer attempt independent and still pass deterministic scoring.
    module = _load_eval_module()
    case = next(case for case in module.load_cases(CASES_PATH) if case.id == "math-direct-002")
    evidence = {
        "objective_id": "objective-1",
        "activity_id": "activity-1",
        "learner_response_ref": "chat-message:1:user",
        "rubric": [{"id": "explain", "passed": True}],
        "outcome": "correct",
        "help_level": 4,
        "source_refs": [],
        "timestamp": 1.0,
        "confidence": 0.9,
        "independent": True,
    }

    score = module.score_contracts(
        case,
        _complete_result(module, case, final_help_level=4, evidence=evidence),
    )

    assert "complete_answer_marked_independent" in score.failures


def test_local_runner_uses_production_finalization_and_persisted_evidence(
    tmp_path, monkeypatch
) -> None:
    # Production break caught: the evaluator calls the orchestrator directly
    # and manufactures an evidence-shaped dictionary without executor finish.
    module = _load_eval_module()
    case = next(case for case in module.load_cases(CASES_PATH) if case.id == "math-lesson-004")
    from deeptutor.learning.coordinator import LearningCoordinator

    async def controlled_prepare(self, payload, available_capabilities, llm_config):
        del self, available_capabilities, llm_config
        return _learning_decision(scope="lesson", goal=str(payload["content"]))

    async def assessed_handle(self, context):
        del self
        context.extension("learning_coordinator")["result"] = {
            "artifact_ref": "",
            "source_refs": [],
            "assessment": {
                "outcome": "correct",
                "rubric": [{"id": "explain", "passed": True}],
                "cited_evidence": ["help me understand"],
                "uncertainty": 0.1,
            },
        }
        for event in _completed_events("The direct answer is an invariant direction."):
            yield event

    monkeypatch.setattr(LearningCoordinator, "prepare_payload", controlled_prepare)
    _patch_orchestrator(monkeypatch, assessed_handle)
    _patch_llm_config(monkeypatch)
    _patch_runtime_support(monkeypatch)

    result = asyncio.run(_local_runner(module, tmp_path).run(case, "active", _fixture(), None))

    assert result.status == "completed", _raw_events(tmp_path, result)
    assert result.decision_persisted is True
    assert result.persisted_evidence_count == 1
    assert result.evidence is not None
    assert result.evidence["outcome"] == "correct"
    assert result.evidence["response"].startswith("Help me understand")
    assert result.thread_state["thread_id"]
    assert result.activity_consumed is True


def test_local_runner_observes_invalid_assessment_fail_closed_after_execution(
    tmp_path, monkeypatch
) -> None:
    # Production break caught: invalid assessment handling is inferred from a
    # synthetic validator call instead of the persisted production outcome.
    module = _load_eval_module()
    case = next(case for case in module.load_cases(CASES_PATH) if case.id == "math-lesson-004")
    from deeptutor.learning.coordinator import LearningCoordinator

    async def controlled_prepare(self, payload, available_capabilities, llm_config):
        del self, available_capabilities, llm_config
        return _learning_decision(scope="lesson", goal=str(payload["content"]))

    async def invalid_assessment_handle(self, context):
        del self
        context.extension("learning_coordinator")["result"] = {
            "artifact_ref": "",
            "source_refs": [],
            "assessment": {
                "outcome": "correct",
                "rubric": [{"id": "explain", "passed": True}],
                "cited_evidence": ["not present in the learner response"],
                "uncertainty": 0.1,
            },
        }
        for event in _completed_events():
            yield event

    monkeypatch.setattr(LearningCoordinator, "prepare_payload", controlled_prepare)
    _patch_orchestrator(monkeypatch, invalid_assessment_handle)
    _patch_llm_config(monkeypatch)
    _patch_runtime_support(monkeypatch)

    result = asyncio.run(_local_runner(module, tmp_path).run(case, "active", _fixture(), None))

    assert result.status == "completed", _raw_events(tmp_path, result)
    assert result.assessment_valid is False
    assert result.persisted_evidence_count == 1
    assert result.evidence is not None and result.evidence["outcome"] == "unassessed"
    assert result.mastery_before == result.mastery_after


def test_local_runner_executes_the_server_owned_delayed_activity(
    tmp_path, monkeypatch
) -> None:
    # Production break caught: resumed evaluation replaces the stored activity
    # with server_next_activity=None and then credits the unchanged fixture.
    module = _load_eval_module()
    case = next(
        case
        for case in module.load_cases(CASES_PATH)
        if case.id == "math-delayed-recall-010"
    )
    from deeptutor.learning.coordinator import LearningCoordinator
    from deeptutor.learning.coordinator.models import ActivityPlan

    observed: dict[str, object] = {}

    async def resumed_prepare(self, payload, available_capabilities, llm_config):
        del self, available_capabilities, llm_config
        server_activity = payload["learning_state"]["server_next_activity"]
        observed["server_next_activity"] = server_activity
        return _learning_decision(
            scope="lesson",
            goal=str(payload["content"]),
        ).model_copy(update={"activity": ActivityPlan.model_validate(server_activity)})

    async def assessed_handle(self, context):
        del self
        context.extension("learning_coordinator")["result"] = {
            "artifact_ref": "",
            "source_refs": [],
            "assessment": {
                "outcome": "correct",
                "rubric": [{"id": "recall", "passed": True}],
                "cited_evidence": ["Without looking at notes"],
                "uncertainty": 0.1,
            },
        }
        for event in _completed_events("The recalled formula was assessed."):
            yield event

    monkeypatch.setattr(LearningCoordinator, "prepare_payload", resumed_prepare)
    _patch_orchestrator(monkeypatch, assessed_handle)
    _patch_llm_config(monkeypatch)
    _patch_runtime_support(monkeypatch)

    result = asyncio.run(
        _local_runner(module, tmp_path).run(
            case,
            "active",
            _fixture(),
            dict(case.stored_turn_state),
        )
    )

    assert observed["server_next_activity"] == case.stored_turn_state["next_activity"]
    assert result.activity_consumed is True
    assert result.persisted_evidence_count == 1
    assert result.evidence is not None
    assert result.evidence["recipe_id"] == "memory-retrieval"
    assert result.evidence["independent"] is True
    assert result.thread_state["status"] == "completed"


def test_reviewer_and_machine_artifacts_are_blind_sealed_and_round_trip(
    tmp_path,
) -> None:
    # Production break caught: one report exposes named contract summaries next
    # to A/B rows and discards the authoritative unblinding map.
    module = _load_eval_module()
    rubric = module.load_rubric(RUBRIC_PATH)
    case = next(case for case in module.load_cases(CASES_PATH) if case.id == "math-answer-001")

    class ControlledRunner:
        async def run(self, requested_case, coordinator_mode, fixture, resume_state):
            del requested_case, fixture, resume_state
            return module.EvalResult(
                status="completed",
                scope="answer",
                route="chat",
                route_available=True,
                raw_output_ref=(
                    "raw/80ae3f4a.json"
                    if coordinator_mode == "off"
                    else "raw/c13d902b.json"
                ),
                review_material=(
                    "Opaque response one"
                    if coordinator_mode == "off"
                    else "Opaque response two"
                ),
                latency_ms=1.0 if coordinator_mode == "off" else 2.0,
                token_usage={"total_tokens": 1 if coordinator_mode == "off" else 2},
            )

    artifacts = asyncio.run(
        module.run_paired_evaluation(
            [case],
            runner=ControlledRunner(),
            rubric=rubric,
            model="test-model",
            settings={"temperature": 0.0},
            provider_seed=317,
            seed_supported=True,
        )
    )
    reviewer = artifacts["reviewer"]
    machine = artifacts["machine"]
    serialized_reviewer = json.dumps(reviewer, sort_keys=True)
    for forbidden in (
        "baseline",
        "coordinator",
        "contract_failures",
        "deterministic_passed",
        "route_available",
        "final_help_level",
        "provider_seed",
    ):
        assert forbidden not in serialized_reviewer
    assert set(reviewer["cases"][0]["blinded_outputs"]) == {"A", "B"}
    assert set(machine["cases"][0]["label_mapping"].values()) == {
        "baseline",
        "coordinator",
    }

    output = tmp_path / "paired.json"
    module.write_paired_artifacts(output, artifacts)
    machine_path = tmp_path / "paired.machine.json"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(machine_path.stat().st_mode) == 0o600
    assert json.loads(output.read_text()) == reviewer
    assert json.loads(machine_path.read_text()) == machine

    with pytest.raises(ValueError, match="scores are not locked"):
        module.unblind_scored_review(output, machine_path)

    scored = json.loads(output.read_text())
    for scores in scored["cases"][0]["human_rubric"].values():
        for dimension in scores:
            scores[dimension] = 3
    output.write_text(json.dumps(scored), encoding="utf-8")
    unblinded = module.unblind_scored_review(output, machine_path)
    assert set(unblinded["cases"][0]["results"]) == {"baseline", "coordinator"}
    for label, mode in machine["cases"][0]["label_mapping"].items():
        assert (
            unblinded["cases"][0]["results"][mode]["review_material"]
            == reviewer["cases"][0]["blinded_outputs"][label]["content"]
        )
        assert (
            unblinded["cases"][0]["results"][mode]["raw_output_ref"]
            == machine["cases"][0]["results"][mode]["raw_output_ref"]
        )
