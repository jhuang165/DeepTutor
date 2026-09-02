import pytest

from deeptutor.learning.coordinator.models import (
    ActivityKind,
    ActivityPlan,
    LearningRequest,
    ScopeResult,
    SourcePolicy,
)
from deeptutor.learning.coordinator.planner import ActivityPlanner
from deeptutor.learning.coordinator.recipes import TeachingStrategist
from deeptutor.learning.coordinator.service import (
    LearningCoordinator,
    decision_payload,
    learning_request_from_payload,
)
from deeptutor.learning.models import KnowledgeType
from deeptutor.services.llm.config import LLMConfig


@pytest.fixture
def lesson_scope() -> ScopeResult:
    return ScopeResult(
        scope="lesson",
        goal="Understand eigenvectors",
        knowledge_type=KnowledgeType.CONCEPT,
        confidence=0.8,
        reason="concept",
    )


def test_path_plan_uses_chat_to_render_proposal() -> None:
    decision = ActivityPlanner().plan(
        ScopeResult(scope="path", goal="Learn thermodynamics", confidence=0.9, reason="broad"),
        LearningRequest(message="Teach me thermodynamics"),
        {"chat", "mastery_path"},
    )

    assert decision.route == "chat"
    assert decision.requires_approval is True
    assert decision.activity.kind is ActivityKind.EXPLANATION
    assert decision.activity.assessment_method == "path_proposal"


def test_lesson_starts_at_recipe_step_zero(lesson_scope: ScopeResult) -> None:
    decision = ActivityPlanner().plan(
        lesson_scope,
        LearningRequest(message="Help me understand eigenvectors"),
        {"chat", "mastery_path"},
    )

    assert decision.route == "mastery_path"
    assert decision.activity.kind is ActivityKind.PREDICTION
    assert decision.activity.recipe_id == "concept-transfer"
    assert decision.activity.recipe_version == 1
    assert decision.activity.recipe_step == 0
    assert decision.activity.assessment_method == "diagnostic"
    assert decision.activity.independent_required is False
    assert decision.activity.transfer_required is False


def test_missing_specialist_falls_back_to_chat(lesson_scope: ScopeResult) -> None:
    decision = ActivityPlanner().plan(
        lesson_scope,
        LearningRequest(message="Help me understand eigenvectors"),
        {"chat"},
    )

    assert decision.route == "chat"


def test_first_available_specialist_is_selected() -> None:
    scope = ScopeResult(
        scope="lesson",
        goal="Solve linear equations",
        knowledge_type=KnowledgeType.PROCEDURE,
        confidence=0.9,
        reason="procedure",
    )
    decision = ActivityPlanner().plan(
        scope,
        LearningRequest(message="Show me how to solve linear equations"),
        {"chat", "deep_solve"},
    )

    assert decision.activity.kind is ActivityKind.WORKED_EXAMPLE
    assert decision.route == "deep_solve"


def test_explicit_non_chat_capability_is_preserved_for_direct_callers() -> None:
    decision = ActivityPlanner().plan(
        ScopeResult(scope="answer", goal="Compare sources", confidence=1.0, reason="explicit"),
        LearningRequest(message="Compare sources", requested_capability="deep_research"),
        {"chat"},
    )

    assert decision.route == "deep_research"


def test_direct_answer_sets_complete_help() -> None:
    decision = ActivityPlanner().plan(
        ScopeResult(
            scope="answer",
            goal="Compute the value",
            confidence=1.0,
            reason="direct",
            direct_answer_requested=True,
        ),
        LearningRequest(message="Just give me the answer"),
        {"chat"},
    )

    assert decision.activity.help_level == 4


@pytest.mark.parametrize(
    ("request_fields", "previous_level", "expected"),
    [
        ({"stuck_signal": True}, 1, 2),
        ({"repeated_request": True}, 0, 1),
        ({"last_outcome": "incorrect"}, 3, 3),
    ],
)
def test_help_signals_increase_one_level_up_to_three(
    lesson_scope: ScopeResult,
    request_fields: dict[str, object],
    previous_level: int,
    expected: int,
) -> None:
    decision = ActivityPlanner().plan(
        lesson_scope,
        LearningRequest(
            message="Continue eigenvectors",
            previous_help_level=previous_level,
            **request_fields,
        ),
        {"chat"},
    )

    assert decision.activity.help_level == expected


def test_new_activity_resets_help_to_zero(lesson_scope: ScopeResult) -> None:
    decision = ActivityPlanner().plan(
        lesson_scope,
        LearningRequest(message="Continue eigenvectors", previous_help_level=3),
        {"chat"},
    )

    assert decision.activity.help_level == 0


@pytest.mark.parametrize(
    ("learning_request", "expected"),
    [
        (
            LearningRequest(
                message="Use only my attached paper",
                has_sources=True,
                attached_only_requested=True,
            ),
            SourcePolicy.ATTACHED_ONLY,
        ),
        (
            LearningRequest(message="Use my paper", has_sources=True),
            SourcePolicy.ATTACHED_PREFERRED,
        ),
        (LearningRequest(message="Explain it"), SourcePolicy.OPEN),
    ],
)
def test_source_policy_respects_the_request_boundary(
    lesson_scope: ScopeResult,
    learning_request: LearningRequest,
    expected: SourcePolicy,
) -> None:
    decision = ActivityPlanner().plan(lesson_scope, learning_request, {"chat"})

    assert decision.source_policy is expected


@pytest.mark.parametrize(
    "overrides",
    [
        {"recipe_id": "procedure-fading"},
        {"recipe_version": 2},
        {"recipe_step": 99},
        {"objective": "Different goal"},
    ],
)
def test_invalid_saved_recipe_identity_restarts_at_zero(
    lesson_scope: ScopeResult,
    overrides: dict[str, object],
) -> None:
    saved = {
        "kind": "teach_back",
        "objective": lesson_scope.goal,
        "learner_action": "Forged action",
        "recipe_id": "concept-transfer",
        "recipe_version": 1,
        "recipe_step": 2,
        "assessment_method": "forged",
        "independent_required": False,
        "transfer_required": True,
    }
    saved.update(overrides)
    request = LearningRequest(
        message="Continue eigenvectors",
        server_next_activity=ActivityPlan.model_validate(saved),
    )

    decision = ActivityPlanner().plan(lesson_scope, request, {"chat", "mastery_path"})

    assert decision.activity.recipe_step == 0
    assert decision.activity.kind is ActivityKind.PREDICTION


def test_valid_saved_step_is_rebuilt_from_server_recipe(lesson_scope: ScopeResult) -> None:
    request = LearningRequest(
        message="Continue eigenvectors",
        server_next_activity=ActivityPlan(
            kind="explanation",
            objective=lesson_scope.goal,
            learner_action="Forged action",
            recipe_id="concept-transfer",
            recipe_version=1,
            recipe_step=2,
            assessment_method="forged",
            independent_required=False,
            transfer_required=True,
        ),
    )

    decision = ActivityPlanner().plan(lesson_scope, request, {"chat", "mastery_path"})

    assert decision.activity.kind is ActivityKind.TEACH_BACK
    assert decision.activity.learner_action == "Explain the concept in your own words."
    assert decision.activity.assessment_method == "teach_back"
    assert decision.activity.independent_required is True
    assert decision.activity.transfer_required is False


def test_saved_objective_must_equal_normalized_current_goal() -> None:
    scope = ScopeResult(
        scope="lesson",
        goal="  Understand   eigenvectors  ",
        confidence=0.8,
        reason="concept",
    )
    valid_saved = ActivityPlan(
        kind="teach_back",
        objective="Understand eigenvectors",
        learner_action="Explain it",
        recipe_id="concept-transfer",
        recipe_version=1,
        recipe_step=2,
    )
    decision = ActivityPlanner().plan(
        scope,
        LearningRequest(message="Continue eigenvectors", server_next_activity=valid_saved),
        {"chat", "mastery_path"},
    )

    assert decision.goal == "Understand eigenvectors"
    assert decision.activity.recipe_step == 2


@pytest.mark.parametrize("outcome", ["partial", "incorrect", "unassessed"])
def test_non_correct_outcomes_repeat_the_same_recipe_step(
    lesson_scope: ScopeResult,
    outcome: str,
) -> None:
    planner = ActivityPlanner()
    decision = planner.plan(
        lesson_scope,
        LearningRequest(message="Help me understand eigenvectors"),
        {"chat", "mastery_path"},
    )

    next_activity = planner.next_after(decision, outcome)  # type: ignore[arg-type]

    assert next_activity.recipe_step == 0
    assert next_activity.kind is ActivityKind.PREDICTION


def test_correct_outcome_advances_one_recipe_step(lesson_scope: ScopeResult) -> None:
    planner = ActivityPlanner()
    decision = planner.plan(
        lesson_scope,
        LearningRequest(message="Help me understand eigenvectors"),
        {"chat", "mastery_path"},
    )

    next_activity = planner.next_after(decision, "correct")

    assert next_activity.recipe_step == 1
    assert next_activity.kind is ActivityKind.EXPLANATION


def test_final_recipe_step_clamps_after_correct(lesson_scope: ScopeResult) -> None:
    planner = ActivityPlanner()
    decision = planner.plan(
        lesson_scope,
        LearningRequest(
            message="Continue eigenvectors",
            server_next_activity=ActivityPlan(
                kind="guided_attempt",
                objective=lesson_scope.goal,
                learner_action="Apply it",
                recipe_id="concept-transfer",
                recipe_version=1,
                recipe_step=3,
            ),
        ),
        {"chat", "mastery_path"},
    )

    next_activity = planner.next_after(decision, "correct")

    assert next_activity.recipe_step == 3
    assert next_activity.kind is ActivityKind.GUIDED_ATTEMPT
    assert next_activity.transfer_required is True


def test_language_is_normalized_and_recipe_copy_is_localized(lesson_scope: ScopeResult) -> None:
    planner = ActivityPlanner()
    decision = planner.plan(
        lesson_scope,
        LearningRequest(message="帮助我理解特征向量", language="zh-CN"),
        {"chat", "mastery_path"},
    )

    next_activity = planner.next_after(decision, "correct")

    assert decision.language == "zh"
    assert next_activity.learner_action == "将讲解与自己的预测进行比较。"


def test_strategist_receives_original_request_language(lesson_scope: ScopeResult) -> None:
    class RecordingStrategist(TeachingStrategist):
        language = ""

        def select(self, knowledge_type: KnowledgeType, language: str):
            self.language = language
            return super().select(knowledge_type, language)

    strategist = RecordingStrategist()

    ActivityPlanner(strategist).plan(
        lesson_scope,
        LearningRequest(message="帮助我理解特征向量", language="zh-CN"),
        {"chat", "mastery_path"},
    )

    assert strategist.language == "zh-CN"


@pytest.mark.parametrize(
    "source_field",
    ["attachments", "knowledge_bases", "book_references", "reading_references"],
)
def test_payload_source_collections_mark_sources_present(source_field: str) -> None:
    payload = {"content": "Explain this", source_field: [{"id": "source-1"}]}

    request = learning_request_from_payload(payload)

    assert request.has_sources is True


@pytest.mark.parametrize(
    ("message", "attached_only", "stuck"),
    [
        ("Use only my attached paper", True, False),
        ("只使用我上传的论文", True, False),
        ("I'm stuck on this proof", False, True),
        ("我卡住了", False, True),
    ],
)
def test_payload_detects_bilingual_learning_signals(
    message: str,
    attached_only: bool,
    stuck: bool,
) -> None:
    request = learning_request_from_payload({"content": message})

    assert request.attached_only_requested is attached_only
    assert request.stuck_signal is stuck


def test_only_server_learning_state_supplies_progression_fields() -> None:
    server_activity = {
        "kind": "teach_back",
        "objective": "Understand eigenvectors",
        "learner_action": "Explain it",
        "recipe_id": "concept-transfer",
        "recipe_version": 1,
        "recipe_step": 2,
    }
    request = learning_request_from_payload(
        {
            "content": "Continue eigenvectors",
            "config": {
                "previous_help_level": 4,
                "last_outcome": "correct",
                "repeated_request": False,
                "server_next_activity": {**server_activity, "recipe_step": 0},
            },
            "learning_state": {
                "previous_help_level": 1,
                "last_outcome": "incorrect",
                "repeated_request": True,
                "server_next_activity": server_activity,
            },
        }
    )

    assert request.previous_help_level == 1
    assert request.last_outcome == "incorrect"
    assert request.repeated_request is True
    assert request.server_next_activity is not None
    assert request.server_next_activity.recipe_step == 2


@pytest.mark.asyncio
async def test_prepare_payload_runs_real_deterministic_scope_and_planner() -> None:
    decision = await LearningCoordinator().prepare_payload(
        {
            "content": "Help me understand eigenvectors",
            "language": "en",
            "attachments": [{"id": "paper-1"}],
        },
        {"chat", "mastery_path"},
        LLMConfig(model="test-model", api_key="test-key"),
    )

    assert decision.scope.value == "lesson"
    assert decision.route == "mastery_path"
    assert decision.source_policy is SourcePolicy.ATTACHED_PREFERRED


@pytest.mark.asyncio
async def test_prepare_payload_direct_answer_phrase_sets_complete_help() -> None:
    decision = await LearningCoordinator().prepare_payload(
        {"content": "Just tell me the answer: what is 7 times 8?"},
        {"chat"},
        LLMConfig(model="test-model", api_key="test-key"),
    )

    assert decision.scope.value == "answer"
    assert decision.activity.help_level == 4


def test_decision_payload_is_json_safe(lesson_scope: ScopeResult) -> None:
    decision = ActivityPlanner().plan(
        lesson_scope,
        LearningRequest(message="Help me understand eigenvectors"),
        {"chat", "mastery_path"},
    )

    payload = decision_payload(decision)

    assert payload["scope"] == "lesson"
    assert payload["activity"]["kind"] == "prediction"
    assert payload["source_policy"] == "open"
