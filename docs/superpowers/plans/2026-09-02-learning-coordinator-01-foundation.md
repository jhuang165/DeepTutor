# Learning Coordinator Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add typed teaching decisions, recipe selection, scope detection, one-activity planning, and shadow-mode runtime integration without changing current learner-visible routing.

**Architecture:** A new `deeptutor.learning.coordinator` package owns pure decision models and services. `TurnRequestPreparer` runs it before capability validation so the selected route, tools, leases, and stored turn agree; the executor copies the decision into `UnifiedContext.extension("learning_coordinator")`. Phase 1 defaults to `off` and supports `shadow`, which records the decision but keeps the requested capability.

**Tech Stack:** Python 3.11-3.14, Pydantic v2, PyYAML, pytest, pytest-asyncio, existing capability registry and runtime settings.

**Spec:** `docs/superpowers/specs/2026-09-02-learning-coordinator-design.md`

## Global Constraints

- Keep `ChatOrchestrator`, `StreamBus`, capability lifecycle, and existing stores authoritative.
- An explicit capability or workspace binding always wins.
- Low-confidence classification uses the smallest safe scope.
- The planner selects one main activity per turn.
- Phase 1 cannot change learner-visible routing; it only supports `off` and `shadow`.
- Store mutable coordinator state under `UnifiedContext.extension("learning_coordinator")`, not compatibility metadata.
- English and Chinese prompt assets must stay in parity.
- Do not add a new third-party dependency.

---

## File map

Create:

- `deeptutor/learning/coordinator/__init__.py`: public imports.
- `deeptutor/learning/coordinator/models.py`: enums and Pydantic contracts.
- `deeptutor/learning/coordinator/recipes.py`: recipe loading and type selection.
- `deeptutor/learning/coordinator/strategies.py`: objective-type to recipe decision.
- `deeptutor/learning/coordinator/recipes/en.yaml`: English teaching recipes.
- `deeptutor/learning/coordinator/recipes/zh.yaml`: Chinese teaching recipes with the same IDs.
- `deeptutor/learning/coordinator/scope.py`: deterministic signals and optional structured classifier.
- `deeptutor/learning/coordinator/prompts.py`: localized scope-classifier prompt loading.
- `deeptutor/learning/coordinator/prompts/en.yaml`: English classifier contract.
- `deeptutor/learning/coordinator/prompts/zh.yaml`: matching Chinese classifier contract.
- `deeptutor/learning/coordinator/planner.py`: route and activity selection.
- `deeptutor/learning/coordinator/service.py`: idempotent `prepare_payload` service.
- `tests/learning/coordinator/test_models_and_recipes.py`: contract and recipe tests.
- `tests/learning/coordinator/test_scope.py`: classification tests.
- `tests/learning/coordinator/test_planner.py`: route and help-policy tests.
- `tests/learning/coordinator/test_shadow_runtime.py`: settings and request-preparation integration.

Modify:

- `deeptutor/services/config/runtime_settings.py`: `learning_coordinator_mode` setting.
- `deeptutor/services/session/turns/request_preparer.py`: invoke coordinator in off/shadow mode.
- `deeptutor/services/session/turns/executor.py`: expose the prepared decision through `extension_state`.
- `tests/services/config/test_runtime_settings.py`: normalization and default coverage.

## Task 1: Typed decisions and teaching recipes

**Files:**

- Create: `deeptutor/learning/coordinator/__init__.py`
- Create: `deeptutor/learning/coordinator/models.py`
- Create: `deeptutor/learning/coordinator/recipes.py`
- Create: `deeptutor/learning/coordinator/strategies.py`
- Create: `deeptutor/learning/coordinator/recipes/en.yaml`
- Create: `deeptutor/learning/coordinator/recipes/zh.yaml`
- Test: `tests/learning/coordinator/test_models_and_recipes.py`

**Interfaces:**

- Produces: `LearningScope`, `ActivityKind`, `SourcePolicy`, `LearningRequest`, `ScopeResult`, `ActivityPlan`, `LearningDecision`, `TeachingRecipe`.
- Produces: `load_recipes(language: str) -> dict[str, TeachingRecipe]`.
- Produces: `recipe_for_knowledge_type(knowledge_type: KnowledgeType, language: str) -> TeachingRecipe`.
- Produces: `TeachingStrategist.select(knowledge_type: KnowledgeType, language: str) -> TeachingRecipe`.
- Depends on: `deeptutor.learning.models.KnowledgeType` and packaged YAML resources.

- [ ] **Step 1: Write failing model and recipe tests**

```python
from pydantic import ValidationError
import pytest

from deeptutor.learning.coordinator.models import (
    ActivityKind,
    ActivityPlan,
    LearningDecision,
    LearningScope,
    SourcePolicy,
)
from deeptutor.learning.coordinator.recipes import load_recipes, recipe_for_knowledge_type
from deeptutor.learning.models import KnowledgeType


def test_activity_plan_rejects_help_outside_ladder() -> None:
    with pytest.raises(ValidationError):
        ActivityPlan(
            kind=ActivityKind.PREDICTION,
            objective="Predict the spectrum",
            learner_action="Choose a spectrum",
            help_level=5,
        )


def test_decision_requires_approval_only_for_path() -> None:
    plan = ActivityPlan(
        kind=ActivityKind.EXPLANATION,
        objective="Explain one fact",
        learner_action="Read and ask a follow-up",
    )
    with pytest.raises(ValidationError):
        LearningDecision(
            scope=LearningScope.ANSWER,
            route="chat",
            goal="One fact",
            activity=plan,
            reason="Narrow request",
            confidence=1.0,
            requires_approval=True,
            source_policy=SourcePolicy.OPEN,
        )


def test_recipe_catalogs_have_identical_ids() -> None:
    assert set(load_recipes("en")) == set(load_recipes("zh"))


@pytest.mark.parametrize("kind", list(KnowledgeType))
def test_each_knowledge_type_has_a_recipe(kind: KnowledgeType) -> None:
    recipe = recipe_for_knowledge_type(kind, "en")
    assert recipe.knowledge_type == kind
    assert recipe.activity_sequence
    assert recipe.evidence_requirements
```

- [ ] **Step 2: Run the test and confirm the package is missing**

Run: `pytest tests/learning/coordinator/test_models_and_recipes.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'deeptutor.learning.coordinator'`.

- [ ] **Step 3: Add the Pydantic contracts**

Implement these exact public shapes in `models.py`:

```python
class LearningScope(str, Enum):
    ANSWER = "answer"
    LESSON = "lesson"
    PATH = "path"


class ActivityKind(str, Enum):
    EXPLANATION = "explanation"
    PREDICTION = "prediction"
    WORKED_EXAMPLE = "worked_example"
    GUIDED_ATTEMPT = "guided_attempt"
    RETRIEVAL = "retrieval"
    TEACH_BACK = "teach_back"
    EVIDENCE_COMPARISON = "evidence_comparison"
    PROJECT_STEP = "project_step"
    REVIEW = "review"
    VISUAL_EXPLORATION = "visual_exploration"


class SourcePolicy(str, Enum):
    ATTACHED_ONLY = "attached_only"
    ATTACHED_PREFERRED = "attached_preferred"
    OPEN = "open"


class LearningRequest(BaseModel):
    message: str = Field(max_length=100_000)
    requested_capability: str = Field(default="chat", max_length=64)
    language: str = "en"
    has_sources: bool = False
    attached_only_requested: bool = False
    course_id: str = ""
    mastery_path_id: str = ""
    workspace_mode: str = ""
    direct_answer_requested: bool = False
    stuck_signal: bool = False
    repeated_request: bool = False
    previous_help_level: int = Field(default=0, ge=0, le=4)
    last_outcome: Literal["", "correct", "partial", "incorrect", "unassessed"] = ""
    server_next_activity: ActivityPlan | None = None


class ScopeResult(BaseModel):
    scope: LearningScope
    goal: str = Field(max_length=2_000)
    knowledge_type: KnowledgeType = KnowledgeType.CONCEPT
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(max_length=500)
    direct_answer_requested: bool = False


class ActivityPlan(BaseModel):
    kind: ActivityKind
    objective: str = Field(max_length=2_000)
    learner_action: str = Field(max_length=4_000)
    knowledge_type: KnowledgeType = KnowledgeType.CONCEPT
    recipe_id: str = "concept-transfer"
    recipe_version: int = Field(default=1, ge=1)
    recipe_step: int = Field(default=0, ge=0)
    help_level: int = Field(default=0, ge=0, le=4)
    source_refs: list[str] = Field(default_factory=list)
    assessment_method: str = "none"
    independent_required: bool = False
    transfer_required: bool = False
    next_action: str = ""


class LearningDecision(BaseModel):
    scope: LearningScope
    route: str = Field(max_length=64)
    goal: str = Field(max_length=2_000)
    language: Literal["en", "zh"] = "en"
    thread_id: str = Field(default="", max_length=128)
    objective_id: str = ""
    activity: ActivityPlan
    reason: str = Field(max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    requires_approval: bool = False
    source_policy: SourcePolicy = SourcePolicy.OPEN

    @model_validator(mode="after")
    def validate_approval(self) -> "LearningDecision":
        if self.requires_approval != (self.scope is LearningScope.PATH):
            raise ValueError("Only path scope requires approval")
        return self
```

Add `RecipeActivity` with `kind`, `assessment_method`, `independent_required`, `transfer_required`, and localized `learner_action` fields. Add `TeachingRecipe` with `id`, `version`, `knowledge_type`, `activity_sequence: list[RecipeActivity]`, `evidence_requirements`, `default_route`, and localized `instruction` fields. Set `extra="forbid"` on every coordinator model. Independence and transfer are recipe-owned booleans; no capability/model result may set them.

- [ ] **Step 4: Add paired recipe catalogs and the loader**

Define four recipe IDs at version `1` in both YAML files: `memory-retrieval`, `concept-transfer`, `procedure-fading`, and `design-critique`. Load with `importlib.resources.files(__package__).joinpath("recipes", language_file)`, normalize non-`zh` languages to `en`, validate every row through `TeachingRecipe`, and raise `ValueError` for duplicate IDs or missing knowledge types. `TeachingStrategist.select` returns the recipe for the supplied knowledge type; it contains no model or storage calls.

Use the same step topology in both languages; only `instruction` and `learner_action` differ:

| Recipe | Ordered `(kind, assessment_method, independent, transfer)` steps |
| --- | --- |
| `memory-retrieval` | `explanation/none/false/false`, `retrieval/retrieval/true/false`, `review/delayed_retrieval/true/false` |
| `concept-transfer` | `prediction/diagnostic/false/false`, `explanation/none/false/false`, `teach_back/teach_back/true/false`, `guided_attempt/transfer_application/true/true` |
| `procedure-fading` | `worked_example/none/false/false`, `guided_attempt/scaffolded_practice/false/false`, `guided_attempt/independent_solution/true/false`, `guided_attempt/transfer_variation/true/true` |
| `design-critique` | `evidence_comparison/source_comparison/false/false`, `project_step/project_artifact/true/false`, `evidence_comparison/independent_critique/true/false` |

```python
@cache
def load_recipes(language: str) -> dict[str, TeachingRecipe]:
    lang = "zh" if str(language).lower().startswith("zh") else "en"
    raw = yaml.safe_load(
        resources.files(__package__).joinpath("recipes", f"{lang}.yaml").read_text("utf-8")
    )
    recipes = [TeachingRecipe.model_validate(item) for item in raw["recipes"]]
    by_id = {recipe.id: recipe for recipe in recipes}
    if len(by_id) != len(recipes):
        raise ValueError("Duplicate teaching recipe id")
    if {recipe.knowledge_type for recipe in recipes} != set(KnowledgeType):
        raise ValueError("Teaching recipe catalog must cover every knowledge type")
    return by_id


class TeachingStrategist:
    def select(self, knowledge_type: KnowledgeType, language: str) -> TeachingRecipe:
        return recipe_for_knowledge_type(knowledge_type, language)
```

- [ ] **Step 5: Run focused tests**

Run: `pytest tests/learning/coordinator/test_models_and_recipes.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit the contracts**

```bash
git add deeptutor/learning/coordinator tests/learning/coordinator/test_models_and_recipes.py
git commit -m "feat(learning): add coordinator contracts and recipes"
```

## Task 2: Scope detection

**Files:**

- Create: `deeptutor/learning/coordinator/scope.py`
- Create: `deeptutor/learning/coordinator/prompts.py`
- Create: `deeptutor/learning/coordinator/prompts/en.yaml`
- Create: `deeptutor/learning/coordinator/prompts/zh.yaml`
- Test: `tests/learning/coordinator/test_scope.py`

**Interfaces:**

- Consumes: `LearningRequest`, `LearningScope`, and `ScopeResult` from Task 1.
- Produces: `StructuredScopeClassifier` protocol with `classify(request: LearningRequest) -> Awaitable[ScopeResult]`.
- Produces: `LLMScopeClassifier`, an isolated `LLMClient` adapter that returns validated `ScopeResult` JSON.
- Produces: `ScopeDetector.detect(request: LearningRequest) -> Awaitable[ScopeResult]`.

- [ ] **Step 1: Write failing classification tests**

```python
@pytest.mark.asyncio
async def test_explicit_capability_is_not_reclassified() -> None:
    result = await ScopeDetector().detect(
        LearningRequest(message="Explain it", requested_capability="deep_solve")
    )
    assert result.scope is LearningScope.ANSWER
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_broad_field_becomes_path() -> None:
    result = await ScopeDetector().detect(
        LearningRequest(message="Teach me undergraduate thermodynamics from scratch")
    )
    assert result.scope is LearningScope.PATH


@pytest.mark.asyncio
async def test_direct_answer_signal_is_preserved() -> None:
    result = await ScopeDetector().detect(
        LearningRequest(message="Just tell me the answer: what is 7 times 8?")
    )
    assert result.scope is LearningScope.ANSWER
    assert result.direct_answer_requested is True


@pytest.mark.asyncio
async def test_narrow_fact_does_not_call_classifier() -> None:
    classifier = AsyncMock()
    result = await ScopeDetector(classifier=classifier).detect(
        LearningRequest(message="What is the derivative of sine?")
    )
    assert result.scope is LearningScope.ANSWER
    classifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_understanding_goal_becomes_lesson() -> None:
    result = await ScopeDetector().detect(
        LearningRequest(message="Help me understand eigenvectors")
    )
    assert result.scope is LearningScope.LESSON


@pytest.mark.asyncio
async def test_classifier_failure_falls_back_to_answer() -> None:
    classifier = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    result = await ScopeDetector(classifier=classifier).detect(
        LearningRequest(message="Eigenvectors keep appearing in my work")
    )
    assert result.scope is LearningScope.ANSWER
    assert result.reason == "classifier_fallback"


@pytest.mark.asyncio
async def test_procedure_language_selects_procedure_recipe_input() -> None:
    result = await ScopeDetector().detect(
        LearningRequest(message="Show me how to solve a second-order differential equation")
    )
    assert result.knowledge_type is KnowledgeType.PROCEDURE


@pytest.mark.asyncio
async def test_llm_classifier_returns_validated_structured_result(monkeypatch) -> None:
    agent = LLMScopeClassifier(
        language="en",
        config=LLMConfig(model="test-model", api_key="test-key"),
    )
    monkeypatch.setattr(
        agent._client,
        "complete",
        AsyncMock(return_value='{"scope":"lesson","goal":"Eigenvectors","knowledge_type":"concept","confidence":0.82,"reason":"dependency-heavy"}'),
    )
    result = await agent.classify(LearningRequest(message="I cannot form a picture of eigenvectors"))
    assert result.scope is LearningScope.LESSON
    assert result.confidence == 0.82
```

- [ ] **Step 2: Run the tests and verify the missing detector failure**

Run: `pytest tests/learning/coordinator/test_scope.py -q`

Expected: collection fails because `ScopeDetector` does not exist.

- [ ] **Step 3: Implement deterministic precedence**

Use this order in `ScopeDetector.detect`:

1. If `requested_capability != "chat"`, return `answer` with confidence `1.0`; routing remains explicit.
2. If `course_id`, `mastery_path_id`, or `workspace_mode` is bound, return `lesson` with confidence `1.0`.
3. Detect direct-answer phrases in English and Chinese, set `direct_answer_requested=True`, and return `answer`.
4. Detect explicit broad curriculum phrases such as `from scratch`, `complete course`, `learning path`, `整个领域`, and `系统学习`; return `path`.
5. Compute a deterministic objective type for every remaining request: define/recall/name maps to memory; solve/calculate/how-to maps to procedure; design/critique/interpret/compare maps to design; otherwise use concept. A valid structured classifier may replace this field.
6. Detect a narrow closed request such as one fact, definition, calculation, translation, or explicit short answer; return `answer` without a model call.
7. Detect an explicit teaching request such as `help me understand`, `teach me`, `walk me through`, `why does`, `帮我理解`, or `讲解`; return `lesson` without a model call.
8. Call the optional structured classifier only for requests not settled by Steps 1-7. On exception, invalid output, or confidence below `0.65`, return `answer` with reason `classifier_fallback` while retaining the deterministic knowledge type.

Implement `LLMScopeClassifier` with an isolated `LLMClient(config, configure_env=False)`. Load paired English/Chinese prompts through `prompts.py`, call `complete` with `temperature=0`, `max_tokens=300`, `response_format={"type": "json_object"}`, parse through `parse_json_response`, and validate through `ScopeResult`. Its prompt must accept only the three scope values and four `KnowledgeType` values. Never include conversation history, memory, attachment bodies, or credentials in this classification call.

```python
class LLMScopeClassifier:
    def __init__(self, language: str, *, config: LLMConfig) -> None:
        self.language = language
        self._client = LLMClient(config, configure_env=False)

    async def classify(self, request: LearningRequest) -> ScopeResult:
        raw = await self._client.complete(
            request.message,
            system_prompt=load_scope_prompt(self.language),
            temperature=0,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        return ScopeResult.model_validate(parse_json_response(raw, fallback=None))
```

Do not infer `lesson` from message length alone.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/learning/coordinator/test_scope.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit scope detection**

```bash
git add deeptutor/learning/coordinator/scope.py deeptutor/learning/coordinator/prompts.py deeptutor/learning/coordinator/prompts tests/learning/coordinator/test_scope.py
git commit -m "feat(learning): classify teaching scope"
```

## Task 3: One-activity planner and coordinator service

**Files:**

- Create: `deeptutor/learning/coordinator/planner.py`
- Create: `deeptutor/learning/coordinator/service.py`
- Modify: `deeptutor/learning/coordinator/__init__.py`
- Test: `tests/learning/coordinator/test_planner.py`

**Interfaces:**

- Consumes: Task 1 contracts, `TeachingStrategist`, `ScopeDetector`, and a `set[str]` of registered capabilities.
- Produces: `ActivityPlanner.plan(scope: ScopeResult, request: LearningRequest, available_capabilities: set[str]) -> LearningDecision`.
- Produces: `ActivityPlanner.next_after(decision: LearningDecision, outcome: Literal["correct", "partial", "incorrect", "unassessed"]) -> ActivityPlan`.
- Produces: `LearningCoordinator.prepare_payload(payload: Mapping[str, Any], available_capabilities: set[str], llm_config: LLMConfig) -> Awaitable[LearningDecision]`.
- Produces: `decision_payload(decision: LearningDecision) -> dict[str, Any]` using `model_dump(mode="json")`.

- [ ] **Step 1: Write failing planner tests**

```python
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


def test_missing_specialist_falls_back_to_chat() -> None:
    decision = ActivityPlanner().plan(
        ScopeResult(scope="lesson", goal="Understand eigenvectors", confidence=0.8, reason="concept"),
        LearningRequest(message="Help me understand eigenvectors"),
        {"chat"},
    )
    assert decision.route == "chat"


def test_direct_answer_sets_complete_help() -> None:
    decision = ActivityPlanner().plan(
        ScopeResult(
            scope="answer",
            goal="Compute the value",
            confidence=1.0,
            reason="direct",
            direct_answer_requested=True,
        ),
        LearningRequest(message="Just give me the answer", direct_answer_requested=True),
        {"chat"},
    )
    assert decision.activity.help_level == 4


def test_stuck_signal_increases_help_one_level() -> None:
    decision = planner.plan(
        lesson_scope,
        LearningRequest(
            message="I'm stuck",
            stuck_signal=True,
            previous_help_level=1,
            last_outcome="incorrect",
        ),
        {"chat"},
    )
    assert decision.activity.help_level == 2


def test_attached_only_request_preserves_source_boundary() -> None:
    decision = planner.plan(
        lesson_scope,
        LearningRequest(
            message="Use only my attached paper",
            has_sources=True,
            attached_only_requested=True,
        ),
        {"chat"},
    )
    assert decision.source_policy is SourcePolicy.ATTACHED_ONLY


def test_invalid_saved_recipe_step_restarts_at_zero() -> None:
    request = LearningRequest(
        message="Continue eigenvectors",
        server_next_activity=ActivityPlan(
            kind="teach_back",
            objective="Different goal",
            learner_action="Explain it",
            recipe_id="concept-transfer",
            recipe_version=1,
            recipe_step=99,
        ),
    )
    decision = planner.plan(lesson_scope, request, {"chat"})
    assert decision.activity.recipe_step == 0
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `pytest tests/learning/coordinator/test_planner.py -q`

Expected: collection fails because the planner does not exist.

- [ ] **Step 3: Implement deterministic route fallback**

Call `TeachingStrategist.select(scope.knowledge_type, request.language)`, normalize and copy the request language onto `LearningDecision`, and choose the first activity in its sequence that fits the scope. If `request.server_next_activity` is present, accept it only after validating the recipe ID, version, step bounds, and objective against the server-selected recipe and goal; otherwise restart from step zero. Copy the recipe ID, version, zero-based step, assessment method, independence requirement, and transfer requirement onto `ActivityPlan`. `next_after` reloads that exact versioned recipe in the decision language, advances one step only after `correct`, repeats the same step after `partial`, `incorrect`, or `unassessed`, and clamps at the final step until its evidence gate passes. Use these route preferences:

```python
ROUTE_PREFERENCES: dict[ActivityKind, tuple[str, ...]] = {
    ActivityKind.EXPLANATION: ("chat",),
    ActivityKind.PREDICTION: ("mastery_path", "chat"),
    ActivityKind.WORKED_EXAMPLE: ("deep_solve", "chat"),
    ActivityKind.GUIDED_ATTEMPT: ("mastery_path", "chat"),
    ActivityKind.RETRIEVAL: ("mastery_path", "chat"),
    ActivityKind.TEACH_BACK: ("mastery_path", "chat"),
    ActivityKind.EVIDENCE_COMPARISON: ("chat",),
    ActivityKind.PROJECT_STEP: ("chat",),
    ActivityKind.REVIEW: ("mastery_path", "chat"),
    ActivityKind.VISUAL_EXPLORATION: ("visualize", "math_animator", "chat"),
}
```

Select the first available route. For `path`, always route to `chat` with `assessment_method="path_proposal"`; path creation happens only through the later approval endpoint. An explicit non-chat capability replaces the planned route after scope selection.

Choose source policy deterministically: `attached_only` when the learner explicitly restricts the answer to supplied material, `attached_preferred` whenever sources are attached or selected, and `open` otherwise. The prompt layer may search only within that boundary.

Help selection is deterministic: direct-answer requests use level 4; a stuck signal, repeated request, or prior incorrect result increases the previous level by one up to level 3; otherwise a new activity starts at level 0. Guided evidence rules in Plan 2 use the resulting level.

```python
def _help_level(request: LearningRequest) -> int:
    if request.direct_answer_requested:
        return 4
    needs_more = request.stuck_signal or request.repeated_request or request.last_outcome == "incorrect"
    return min(3, max(1, request.previous_help_level + 1)) if needs_more else 0
```

- [ ] **Step 4: Implement `LearningCoordinator.prepare_payload`**

Map the validated turn payload into `LearningRequest`, construct `ScopeDetector` with `LLMScopeClassifier(request.language, config=llm_config)` unless a test injects another classifier, call `detect`, then call `ActivityPlanner.plan`. Derive `has_sources` from attachments, knowledge bases, book references, or reading references. Detect paired English/Chinese phrases for an attached-only restriction and for being stuck; accept prior help, outcome, repetition, and `server_next_activity` only from server-owned `payload["learning_state"]`, never from public capability config. Keep the service free of storage and runtime imports.

```python
async def prepare_payload(
    self,
    payload: Mapping[str, Any],
    available_capabilities: set[str],
    llm_config: LLMConfig,
) -> LearningDecision:
    request = learning_request_from_payload(payload)
    detector = self._detector or ScopeDetector(
        classifier=LLMScopeClassifier(request.language, config=llm_config)
    )
    scope = await detector.detect(request)
    return self._planner.plan(scope, request, available_capabilities)
```

- [ ] **Step 5: Run coordinator tests**

Run: `pytest tests/learning/coordinator/test_models_and_recipes.py tests/learning/coordinator/test_scope.py tests/learning/coordinator/test_planner.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit planning service**

```bash
git add deeptutor/learning/coordinator tests/learning/coordinator/test_planner.py
git commit -m "feat(learning): plan one teaching activity"
```

## Task 4: Shadow-mode runtime integration

**Files:**

- Modify: `deeptutor/services/config/runtime_settings.py`
- Modify: `deeptutor/services/session/turns/request_preparer.py`
- Modify: `deeptutor/services/session/turns/executor.py`
- Modify: `tests/services/config/test_runtime_settings.py`
- Create: `tests/learning/coordinator/test_shadow_runtime.py`

**Interfaces:**

- Consumes: `LearningCoordinator.prepare_payload` and `decision_payload` from Task 3.
- Produces: system setting `learning_coordinator_mode: "off" | "shadow" | "active"`, default `"off"`; this plan rejects `"active"` at execution and reserves it for Plan 3.
- Produces: payload field `learning_decision: dict[str, Any] | None` internal to the runtime.
- Produces: `context.extension("learning_coordinator")["decision"]` for downstream capabilities.

- [ ] **Step 1: Write failing setting tests**

```python
def test_learning_coordinator_defaults_off(tmp_path) -> None:
    service = RuntimeSettingsService(settings_dir=tmp_path)
    assert service.load_system()["learning_coordinator_mode"] == "off"


def test_learning_coordinator_mode_normalizes_invalid_value(tmp_path) -> None:
    service = RuntimeSettingsService(settings_dir=tmp_path)
    saved = service.save_system({"learning_coordinator_mode": "invalid"})
    assert saved["learning_coordinator_mode"] == "off"
```

- [ ] **Step 2: Write failing shadow-route integration test**

Create a test patterned after `tests/services/session/test_capability_routing.py`. Patch `LearningCoordinator.prepare_payload` to return a `lesson` decision with route `mastery_path`, set the global mode to `shadow`, run a chat turn, and assert:

```python
assert captured["active_capability"] == "chat"
assert captured["extension_state"]["learning_coordinator"]["decision"]["route"] == "mastery_path"
assert captured["done_metadata"]["learning_decision"]["scope"] == "lesson"
assert session["preferences"]["capability"] == "chat"
```

Also test `off` never constructs the coordinator, configured `active` behaves as `off` until Plan 3, an explicit `deep_solve` request remains `deep_solve` in shadow mode, a reading/course/mastery/selection-tutor binding never constructs the coordinator, and a coordinator exception preserves `chat` while setting `learning_decision_status="failed"`.

- [ ] **Step 3: Run the new tests and verify failure**

Run: `pytest tests/services/config/test_runtime_settings.py tests/learning/coordinator/test_shadow_runtime.py -q`

Expected: failures for the missing setting and missing decision metadata.

- [ ] **Step 4: Add normalized runtime setting**

Add `"learning_coordinator_mode": "off"` to `DEFAULT_SYSTEM_SETTINGS`. In `_normalize_system`, lowercase the value and keep it only when it is one of `{"off", "shadow", "active"}`; otherwise store `"off"`.

```python
mode = str(settings.get("learning_coordinator_mode") or "off").strip().lower()
payload["learning_coordinator_mode"] = mode if mode in {"off", "shadow", "active"} else "off"
```

- [ ] **Step 5: Prepare and preserve the shadow decision**

In `TurnRequestPreparer.start_turn`, run the coordinator only after the existing user-specific `llm_selection` validation. Resolve that selection through `resolve_llm_config_for_selection`; never let classification fall through to an administrator's default provider. Before turn creation:

```python
learning_decision = None
eligible = (
    requested_capability == "chat"
    and not requested_course_id
    and not payload.get("mastery_path_id")
    and not payload.get("workspace_mode")
    and not payload.get("selection_tutor_context")
)
if coordinator_mode == "shadow" and eligible:
    coordinator = LearningCoordinator()
    decision = await coordinator.prepare_payload(
        payload,
        set(get_capability_registry().list_capabilities()),
        resolve_llm_config_for_selection(payload.get("llm_selection")),
    )
    learning_decision = decision_payload(decision)
```

Wrap preparation in `try/except Exception`: log the exception without request content, preserve `capability=requested_capability`, and set `learning_decision_status="failed"`. A coordinator outage must not fail a valid chat turn. Keep the requested capability in shadow mode. Until Plan 3, treat configured `active` mode as `off` and cover that temporary behavior with a regression test. Add `learning_decision` and `learning_decision_status` to the internal execution payload and SESSION/DONE metadata, but do not persist either as a session preference.

In `executor.py`, construct `UnifiedContext` with:

```python
extension_state=(
    {"learning_coordinator": {"decision": dict(payload["learning_decision"])}}
    if isinstance(payload.get("learning_decision"), dict)
    else {}
),
```

Do not put the decision in `context.metadata`.

- [ ] **Step 6: Run focused runtime tests**

Run: `pytest tests/learning/coordinator tests/runtime/test_orchestrator.py tests/services/session/test_capability_routing.py tests/services/config/test_runtime_settings.py -q`

Expected: all tests pass and existing quiz auto-routing behavior remains unchanged.

- [ ] **Step 7: Run lint on touched Python files**

Run: `ruff check deeptutor/learning/coordinator deeptutor/services/session/turns/request_preparer.py deeptutor/services/session/turns/executor.py tests/learning/coordinator`

Expected: no errors.

- [ ] **Step 8: Commit shadow integration**

```bash
git add deeptutor/services/config/runtime_settings.py deeptutor/services/session/turns/request_preparer.py deeptutor/services/session/turns/executor.py tests/services/config/test_runtime_settings.py tests/learning/coordinator
git commit -m "feat(learning): add coordinator shadow mode"
```

## Plan 1 verification

- [ ] Run: `pytest tests/learning/coordinator tests/runtime/test_orchestrator.py tests/services/session/test_capability_routing.py tests/services/config/test_runtime_settings.py -q`
- [ ] Run: `ruff check deeptutor/learning/coordinator deeptutor/services/session/turns/request_preparer.py deeptutor/services/session/turns/executor.py tests/learning/coordinator`
- [ ] Run: `git diff --check HEAD~4..HEAD`
- [ ] Confirm a normal chat turn still records capability `chat` when `learning_coordinator_mode=shadow`.
- [ ] Confirm `git status --short` contains no unexpected files before starting Plan 2.
