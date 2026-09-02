# Learning Evidence and Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist resumable learning threads and inspectable evidence, derive mastery through deterministic rules, and expose one ranked next action without changing existing mastery results for old data.

**Architecture:** Extend `LearningStore` with thread, evidence, and audit-event tables in the existing per-user mastery database. A strict evidence validator converts capability results into `EvidenceRecord`; a policy adapter computes only the coordinator-owned mastery contribution. `LearningQueueService` projects active threads, interrupted interactions, spaced reviews, and accepted paths without storing a second curriculum.

**Tech Stack:** Python 3.11-3.14, Pydantic v2, SQLite, existing `LearningStore` transactions and `LearningService`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-02-learning-coordinator-design.md`

## Global Constraints

- Complete Plan 1 first.
- Keep current quiz attempts, qualitative mastery, learner overrides, path events, review schedules, and leases authoritative.
- Old data must produce identical mastery results before new coordinator evidence exists.
- The LLM never writes a mastery score or mastery boolean.
- Invalid open-ended assessment becomes `unassessed` and cannot reduce mastery.
- Removing evidence marks it removed and appends an audit event; no hard delete.
- Preferences and learner claims remain separate from assessed evidence.
- Store full learner artifacts by reference when they exceed 8,000 characters.

---

## File map

Create:

- `deeptutor/learning/evidence.py`: assessment validation and deterministic evidence gate.
- `deeptutor/learning/queue.py`: ranked next-action projection.
- `tests/learning/coordinator/test_thread_storage.py`: schema, persistence, and audit tests.
- `tests/learning/coordinator/test_evidence_policy.py`: validation and per-type gate tests.
- `tests/learning/coordinator/test_learning_queue.py`: queue ordering and reason tests.
- `tests/learning/coordinator/test_finish_service.py`: coordinator finalization tests.

Modify:

- `deeptutor/learning/models.py`: `LearningThread`, `EvidenceRecord`, supporting enums, and `LearningProgress.evidence_mastery`.
- `deeptutor/learning/storage.py`: schema and transactional thread/evidence methods.
- `deeptutor/learning/policy.py`: combine legacy and coordinator-owned evidence gates.
- `deeptutor/learning/service.py`: evidence recalculation entry point.
- `deeptutor/learning/coordinator/models.py`: queue item and assessment-result contracts.
- `deeptutor/learning/coordinator/service.py`: `finish` and queue handoff.
- `deeptutor/services/session/turns/executor.py`: finalize successful turns after capability output exists.
- `deeptutor/learning/tests/test_models.py`: round-trip defaults.
- `deeptutor/learning/tests/test_policy.py`: legacy compatibility.

## Task 1: Learning thread and evidence persistence

**Files:**

- Modify: `deeptutor/learning/models.py`
- Modify: `deeptutor/learning/storage.py`
- Modify: `deeptutor/learning/tests/test_models.py`
- Create: `tests/learning/coordinator/test_thread_storage.py`

**Interfaces:**

- Produces: `LearningThreadStatus`, `EvidenceOutcome`, `LearningThread`, and `EvidenceRecord`.
- Produces: `LearningStore.create_learning_thread`, `get_learning_thread`, `list_learning_threads`, `set_learning_thread_next_activity`, `complete_learning_thread`.
- Produces: `LearningStore.append_evidence`, `list_evidence`, and `remove_evidence`.

- [ ] **Step 1: Write failing model round-trip tests**

```python
def test_learning_thread_round_trip() -> None:
    thread = LearningThread(
        thread_id="thread-1",
        session_id="session-1",
        scope="lesson",
        goal="Understand eigenvectors",
        status="active",
    )
    assert LearningThread.model_validate_json(thread.model_dump_json()) == thread


def test_evidence_rejects_complete_answer_as_independent() -> None:
    with pytest.raises(ValidationError):
        EvidenceRecord(
            evidence_id="ev-1",
            thread_id="thread-1",
            activity_kind="guided_attempt",
            recipe_id="procedure-fading",
            recipe_version=1,
            outcome="correct",
            help_level=4,
            independent=True,
            transfer=False,
            session_id="session-1",
            turn_id="turn-1",
        )
```

- [ ] **Step 2: Add exact persistence models**

Add:

```python
class LearningThreadStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class EvidenceOutcome(str, Enum):
    CORRECT = "correct"
    PARTIAL = "partial"
    INCORRECT = "incorrect"
    UNASSESSED = "unassessed"


class LearningThread(BaseModel):
    model_config = ConfigDict(extra="ignore")
    thread_id: str
    session_id: str
    scope: Literal["lesson", "path"]
    goal: str
    status: LearningThreadStatus
    path_id: str = ""
    course_id: str = ""
    source_refs: list[str] = Field(default_factory=list)
    next_activity: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    evidence_id: str
    thread_id: str
    path_id: str = ""
    objective_id: str = ""
    activity_kind: str
    recipe_id: str
    recipe_version: int = Field(ge=1)
    response: str = Field(default="", max_length=8_000)
    response_ref: str = ""
    artifact_ref: str = ""
    outcome: EvidenceOutcome
    help_level: int = Field(ge=0, le=4)
    independent: bool = False
    transfer: bool = False
    rubric: list[dict[str, Any]] = Field(default_factory=list)
    cited_evidence: list[str] = Field(default_factory=list)
    uncertainty: float = Field(default=1.0, ge=0.0, le=1.0)
    source_refs: list[str] = Field(default_factory=list)
    session_id: str
    turn_id: str
    created_at: float = Field(default_factory=time.time)
    removed_at: float | None = None

    @model_validator(mode="after")
    def validate_independence(self) -> "EvidenceRecord":
        if self.help_level >= 3 and self.independent:
            raise ValueError("Guided work cannot count as independent")
        return self
```

Add `evidence_mastery: dict[str, bool] = Field(default_factory=dict)` to `LearningProgress`; old JSON loads it as empty.

- [ ] **Step 3: Run model tests and confirm storage methods are missing**

Run: `pytest deeptutor/learning/tests/test_models.py tests/learning/coordinator/test_thread_storage.py -q`

Expected: model tests pass, storage tests fail for missing methods.

- [ ] **Step 4: Add SQLite tables in `LearningStore._ensure_schema`**

Use this schema, following the store's existing connection and transaction helpers:

```sql
CREATE TABLE IF NOT EXISTS learning_threads (
    thread_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    path_id TEXT NOT NULL DEFAULT '',
    course_id TEXT NOT NULL DEFAULT '',
    source_refs_json TEXT NOT NULL DEFAULT '[]',
    next_activity_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learning_threads_session
    ON learning_threads(session_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS learning_evidence (
    evidence_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES learning_threads(thread_id),
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    removed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_learning_evidence_thread
    ON learning_evidence(thread_id, created_at ASC);

CREATE TABLE IF NOT EXISTS learning_audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    session_id TEXT NOT NULL DEFAULT '',
    turn_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
```

- [ ] **Step 5: Implement transactional storage methods**

Use `INSERT` for thread creation, idempotent insert-by-ID for evidence, `UPDATE ... WHERE removed_at IS NULL` for removal, and append `thread.created`, `thread.next_activity`, `thread.completed`, `evidence.appended`, or `evidence.removed` in the same transaction. `append_evidence` must reject a missing thread and append its audit event only when the insert wins; replaying the same evidence ID returns the existing row without a duplicate event. `remove_evidence` must likewise be idempotent and return the stored record with `removed_at` populated without appending a second removal event.

```python
def append_evidence(self, record: EvidenceRecord) -> EvidenceRecord:
    with self._transaction() as conn:
        if conn.execute(
            "SELECT 1 FROM learning_threads WHERE thread_id = ?", (record.thread_id,)
        ).fetchone() is None:
            raise LearningStoreError(f"Unknown learning thread: {record.thread_id}")
        cursor = conn.execute(
            "INSERT OR IGNORE INTO learning_evidence "
            "(evidence_id, thread_id, payload_json, created_at, removed_at) VALUES (?, ?, ?, ?, NULL)",
            (record.evidence_id, record.thread_id, record.model_dump_json(), record.created_at),
        )
        if cursor.rowcount == 1:
            self._append_learning_event(
                conn,
                record.thread_id,
                "evidence.appended",
                {"evidence_id": record.evidence_id},
            )
    return self.get_evidence(record.evidence_id)
```

- [ ] **Step 6: Run storage tests**

Run: `pytest deeptutor/learning/tests/test_models.py deeptutor/learning/tests/test_storage.py tests/learning/coordinator/test_thread_storage.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit persistence**

```bash
git add deeptutor/learning/models.py deeptutor/learning/storage.py deeptutor/learning/tests/test_models.py tests/learning/coordinator/test_thread_storage.py
git commit -m "feat(learning): persist threads and evidence"
```

## Task 2: Evidence validation and deterministic mastery contribution

**Files:**

- Create: `deeptutor/learning/evidence.py`
- Modify: `deeptutor/learning/policy.py`
- Modify: `deeptutor/learning/service.py`
- Modify: `deeptutor/learning/tests/test_policy.py`
- Create: `tests/learning/coordinator/test_evidence_policy.py`

**Interfaces:**

- Consumes: `EvidenceRecord`, `EvidenceOutcome`, `KnowledgeType`, `LearningProgress`.
- Produces: `validate_open_assessment(payload: object, learner_response: str) -> ValidatedAssessment | None`.
- Produces: `evidence_gate(knowledge_type: KnowledgeType, records: Sequence[EvidenceRecord]) -> bool`.
- Produces: `LearningService.recalculate_evidence_mastery(path_id: str, objective_id: str) -> bool`.

- [ ] **Step 1: Write failing assessment-validation tests**

```python
def test_assessment_requires_cited_learner_text() -> None:
    assert validate_open_assessment(
        {
            "outcome": "correct",
            "rubric": [{"id": "mechanism", "passed": True}],
            "cited_evidence": ["words the learner never wrote"],
            "uncertainty": 0.1,
        },
        "Eigenvectors keep their direction under the transform.",
    ) is None


def test_high_uncertainty_is_unassessed() -> None:
    assert validate_open_assessment(
        {
            "outcome": "correct",
            "rubric": [{"id": "mechanism", "passed": True}],
            "cited_evidence": ["keep their direction"],
            "uncertainty": 0.6,
        },
        "They keep their direction.",
    ) is None
```

- [ ] **Step 2: Write failing per-type gate tests**

Cover these exact rules with fixed timestamps:

```python
assert evidence_gate(KnowledgeType.MEMORY, [independent_now, independent_delayed]) is True
assert evidence_gate(KnowledgeType.MEMORY, [independent_now, independent_same_day]) is False
assert evidence_gate(KnowledgeType.CONCEPT, [independent_teach_back]) is True
assert evidence_gate(KnowledgeType.CONCEPT, [guided_teach_back]) is False
assert evidence_gate(KnowledgeType.PROCEDURE, [independent_solution, transfer_variation]) is True
assert evidence_gate(KnowledgeType.DESIGN, [project_artifact, independent_critique]) is True
assert evidence_gate(KnowledgeType.DESIGN, [project_artifact]) is False
```

Use a 20-hour minimum separation for delayed memory evidence so daylight-saving shifts do not affect the test.

- [ ] **Step 3: Implement strict open-assessment validation**

Add internal Pydantic models `RubricCriterionResult` and `ValidatedAssessment`; the latter exposes `rubric: list[RubricCriterionResult]` to match the assessment tool schema. Accept only outcomes `correct`, `partial`, and `incorrect`; require at least one rubric criterion, at least one cited excerpt, `uncertainty <= 0.5`, and every cited excerpt to occur in normalized learner text. Return `None` for any failure.

```python
def validate_open_assessment(payload: object, learner_response: str) -> ValidatedAssessment | None:
    try:
        result = ValidatedAssessment.model_validate(payload)
    except ValidationError:
        return None
    normalized = " ".join(learner_response.casefold().split())
    if result.uncertainty > 0.5 or not result.rubric or not result.cited_evidence:
        return None
    if any(" ".join(item.casefold().split()) not in normalized for item in result.cited_evidence):
        return None
    return result
```

- [ ] **Step 4: Implement `evidence_gate`**

Filter removed and `unassessed` records first. Require `outcome=correct` for gate evidence. Apply the exact knowledge-type rules from Step 2. Help levels 3 and 4 never count as independent. Learner overrides never enter this function.

```python
def evidence_gate(kind: KnowledgeType, records: Sequence[EvidenceRecord]) -> bool:
    valid = [
        row for row in records
        if row.removed_at is None and row.outcome is EvidenceOutcome.CORRECT
    ]
    independent = [row for row in valid if row.independent and row.help_level <= 2]
    if kind is KnowledgeType.MEMORY:
        return has_two_retrievals_separated_by(independent, seconds=20 * 60 * 60)
    if kind is KnowledgeType.CONCEPT:
        return any(row.activity_kind == "teach_back" or row.transfer for row in independent)
    if kind is KnowledgeType.PROCEDURE:
        return has_independent_solution(independent) and any(row.transfer for row in independent)
    return has_project_artifact(valid) and has_independent_critique(independent)
```

- [ ] **Step 5: Preserve legacy policy results**

Change `is_assessed_mastered` to return the current legacy result OR `progress.evidence_mastery.get(kp.id, False)`. Change `display_mastery` to return `1.0` when evidence mastery is true, otherwise preserve current behavior. Add a regression test that serializes an old `LearningProgress` without `evidence_mastery` and gets the same status, display value, and next objective as before.

```python
def is_assessed_mastered(progress: LearningProgress, kp: KnowledgePoint) -> bool:
    evidence_pass = bool(progress.evidence_mastery.get(kp.id, False))
    if kp.type in QUALITATIVE_TYPES:
        return evidence_pass or bool(progress.qualitative_mastery.get(kp.id, False))
    return evidence_pass or progress.mastery_levels.get(kp.id, 0.0) >= gate_threshold(kp.type)
```

- [ ] **Step 6: Add recalculation service**

`LearningService.recalculate_evidence_mastery` loads the path, loads non-removed evidence for the objective's bound thread(s), computes the gate, writes only `progress.evidence_mastery[objective_id]`, rebuilds the review queue if the gate changed, and appends `mastery.evidence_recalculated` to the existing path event stream. Reject unknown objectives.

```python
def recalculate_evidence_mastery(self, path_id: str, objective_id: str) -> bool:
    with self.store.transaction(path_id) as tx:
        kp = require_knowledge_point(tx.progress, objective_id)
        records = self.store.list_evidence(path_id=path_id, objective_id=objective_id)
        passed = evidence_gate(kp.type, records)
        tx.progress.evidence_mastery[objective_id] = passed
        tx.progress.review_queue = self.scheduler.build_review_queue(tx.progress)
        tx.emit("mastery.evidence_recalculated", {"objective_id": objective_id, "passed": passed})
        return passed
```

- [ ] **Step 7: Run policy tests**

Run: `pytest deeptutor/learning/tests/test_policy.py deeptutor/learning/tests/test_service_replace_merge.py tests/learning/coordinator/test_evidence_policy.py -q`

Expected: all tests pass, including the old-data regression.

- [ ] **Step 8: Commit evidence policy**

```bash
git add deeptutor/learning/evidence.py deeptutor/learning/policy.py deeptutor/learning/service.py deeptutor/learning/tests/test_policy.py tests/learning/coordinator/test_evidence_policy.py
git commit -m "feat(learning): derive mastery from evidence"
```

## Task 3: Finalize coordinator turns safely

**Files:**

- Modify: `deeptutor/learning/coordinator/models.py`
- Modify: `deeptutor/learning/coordinator/service.py`
- Modify: `deeptutor/services/session/turns/executor.py`
- Create: `tests/learning/coordinator/test_finish_service.py`

**Interfaces:**

- Produces: `CapabilityLearningResult` with only `artifact_ref`, `assessment`, and `source_refs`; the model cannot supply the learner's response, independence/transfer labels, or next activity.
- Produces: `LearningCoordinator.finish(decision: LearningDecision, result: CapabilityLearningResult, *, session_id: str, turn_id: str, learner_response: str, allowed_source_refs: Collection[str]) -> Awaitable[EvidenceRecord | None]`.
- Depends on: `LearningStore`, `validate_open_assessment`, and `LearningService.recalculate_evidence_mastery`.

- [ ] **Step 1: Write failing finalization tests**

```python
@pytest.mark.asyncio
async def test_finish_does_not_record_answer_scope() -> None:
    assert await coordinator.finish(
        answer_decision,
        result,
        session_id="s",
        turn_id="t",
        learner_response="What is 7 times 8?",
        allowed_source_refs=set(),
    ) is None


@pytest.mark.asyncio
async def test_invalid_assessment_records_unassessed() -> None:
    record = await coordinator.finish(
        lesson_decision,
        CapabilityLearningResult(
            assessment={"outcome": "correct", "cited_evidence": ["not present"]},
        ),
        session_id="s",
        turn_id="t",
        learner_response="My explanation",
        allowed_source_refs=set(),
    )
    assert record is not None
    assert record.outcome is EvidenceOutcome.UNASSESSED
    learning_service.recalculate_evidence_mastery.assert_not_called()


@pytest.mark.asyncio
async def test_finish_is_idempotent_by_turn_and_objective() -> None:
    first = await coordinator.finish(
        decision,
        result,
        session_id="s",
        turn_id="t",
        learner_response="My answer",
        allowed_source_refs=set(),
    )
    second = await coordinator.finish(
        decision,
        result,
        session_id="s",
        turn_id="t",
        learner_response="My answer",
        allowed_source_refs=set(),
    )
    assert second.evidence_id == first.evidence_id


@pytest.mark.asyncio
async def test_long_response_keeps_durable_turn_reference() -> None:
    record = await coordinator.finish(
        decision,
        result,
        session_id="s",
        turn_id="t",
        learner_response="x" * 8_001,
        allowed_source_refs=set(),
    )
    assert record.response_ref == "chat-turn:t:user"
    assert len(record.response) == 8_000


@pytest.mark.asyncio
async def test_finish_drops_unverified_source_ids() -> None:
    result = valid_result.model_copy(
        update={"source_refs": ["attached-1", "invented-9"]}
    )
    record = await coordinator.finish(
        decision,
        result,
        session_id="s",
        turn_id="t",
        learner_response="My answer",
        allowed_source_refs={"attached-1"},
    )
    assert record.source_refs == ["attached-1"]
```

- [ ] **Step 2: Implement `CapabilityLearningResult`**

Set `extra="forbid"`; keep `assessment` as `dict[str, Any] | None` because capability output crosses a trust boundary and validation belongs in `evidence.py`. Do not add `next_activity`, `independent`, `transfer`, `mastery`, or learner-response fields.

```python
class CapabilityLearningResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_ref: str = ""
    assessment: dict[str, Any] | None = None
    source_refs: list[str] = Field(default_factory=list)
```

- [ ] **Step 3: Implement `finish`**

Return `None` for `answer` scope and for a draft `path` decision. Approved-path activities arrive as `lesson` decisions bound to a `path_id`. For a lesson, create or resume the `LearningThread`; when `decision.thread_id` is empty, derive `thread_id` as `sha256(f"{session_id}:{decision.goal}")[:32]`. Validate assessment, derive `EvidenceOutcome`, and use deterministic evidence ID `sha256(f"{turn_id}:{objective_id}:{activity.kind}:{activity.recipe_step}")[:32]`. Copy the recipe identity onto the record. Derive `independent` as `validated is not None and activity.independent_required and help_level <= 2`; derive `transfer` only as `independent and activity.transfer_required`. Intersect model-reported `source_refs` with the executor-supplied `allowed_source_refs`; unknown IDs are dropped and logged without learner text. Keep at most 8,000 characters inline; when the raw learner response is longer, set `response_ref=f"chat-turn:{turn_id}:user"` so the full already-persisted user message remains recoverable. Call `append_evidence`; if the record is assessed and bound to a path objective, call `recalculate_evidence_mastery`. Ask `ActivityPlanner.next_after` for the deterministic next activity and save it only after evidence append succeeds; never accept next-activity fields from capability output.

```python
evidence_id = hashlib.sha256(
    f"{turn_id}:{decision.objective_id}:{decision.activity.kind.value}:"
    f"{decision.activity.recipe_step}".encode()
).hexdigest()[:32]
thread_id = decision.thread_id or hashlib.sha256(
    f"{session_id}:{decision.goal}".encode()
).hexdigest()[:32]
record = EvidenceRecord(
    evidence_id=evidence_id,
    thread_id=thread_id,
    path_id=thread.path_id,
    objective_id=decision.objective_id,
    activity_kind=decision.activity.kind.value,
    recipe_id=decision.activity.recipe_id,
    recipe_version=decision.activity.recipe_version,
    response=learner_response[:8_000],
    response_ref=(f"chat-turn:{turn_id}:user" if len(learner_response) > 8_000 else ""),
    artifact_ref=result.artifact_ref,
    outcome=outcome,
    help_level=decision.activity.help_level,
    independent=(
        validated is not None
        and decision.activity.independent_required
        and decision.activity.help_level <= 2
    ),
    transfer=(
        validated is not None
        and decision.activity.independent_required
        and decision.activity.transfer_required
        and decision.activity.help_level <= 2
    ),
    rubric=(
        [item.model_dump(mode="json") for item in validated.rubric]
        if validated is not None
        else []
    ),
    cited_evidence=(validated.cited_evidence if validated is not None else []),
    uncertainty=(validated.uncertainty if validated is not None else 1.0),
    source_refs=sorted(set(result.source_refs) & set(allowed_source_refs)),
    session_id=session_id,
    turn_id=turn_id,
)
```

- [ ] **Step 4: Wire finalization after successful capability execution**

In `executor.py`, preserve the raw learner message before any workspace/context augmentation. After `assistant_content` and `context.capability_output` are final but before publishing the terminal DONE event, read both the decision and result from the coordinator extension namespace; never infer correctness from prose or validate quoted evidence against augmented context. Build `allowed_source_refs` from the actual `source_index`, persisted attachment/reference IDs resolved for this turn, and citation IDs in structured capability event metadata—not from the model's assessment payload. Catch finalization errors, log them, and add `learning_evidence_status="failed"` to DONE metadata without turning a successful answer into a failed turn.

```python
decision_raw = context.extension("learning_coordinator").get("decision")
result_raw = context.extension("learning_coordinator").get("result")
if isinstance(decision_raw, dict) and isinstance(result_raw, dict):
    try:
        await coordinator.finish(
            LearningDecision.model_validate(decision_raw),
            CapabilityLearningResult.model_validate(result_raw),
            session_id=session_id,
            turn_id=turn_id,
            learner_response=raw_user_content,
            allowed_source_refs=trusted_source_ids_from_turn(
                source_index,
                attachment_records,
                context.capability_output.event_metadata,
            ),
        )
    except Exception:
        logger.exception("Learning evidence finalization failed turn=%s", turn_id)
        done_metadata["learning_evidence_status"] = "failed"
```

- [ ] **Step 5: Run finalization and runtime regression tests**

Run: `pytest tests/learning/coordinator/test_finish_service.py tests/runtime/test_orchestrator.py tests/services/session/test_capability_routing.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit turn finalization**

```bash
git add deeptutor/learning/coordinator/models.py deeptutor/learning/coordinator/service.py deeptutor/services/session/turns/executor.py tests/learning/coordinator/test_finish_service.py
git commit -m "feat(learning): finalize teaching evidence"
```

## Task 4: Learning queue projection

**Files:**

- Create: `deeptutor/learning/queue.py`
- Modify: `deeptutor/learning/coordinator/models.py`
- Create: `tests/learning/coordinator/test_learning_queue.py`

**Interfaces:**

- Produces: `LearningQueueReason` and `LearningQueueItem`.
- Produces: `LearningQueueService.list_items(*, session_id: str = "", limit: int = 10, now: float | None = None) -> list[LearningQueueItem]`.
- Consumes: active threads, active mastery interactions, due `ReviewTask` rows, and `LearningService.list_path_overviews()`.

- [ ] **Step 1: Write failing queue tests**

```python
def test_queue_orders_unfinished_attempt_before_due_review() -> None:
    items = service.list_items(session_id="s", now=1_000.0)
    assert [item.reason for item in items[:2]] == [
        LearningQueueReason.UNFINISHED_ATTEMPT,
        LearningQueueReason.DUE_REVIEW,
    ]


def test_queue_contains_one_item_per_thread_or_path() -> None:
    items = service.list_items(session_id="s")
    identities = [(item.thread_id, item.path_id) for item in items]
    assert len(identities) == len(set(identities))


def test_queue_reason_is_learner_readable() -> None:
    item = service.list_items(session_id="s")[0]
    assert item.reason_text
    assert "unknown" not in item.reason_text.lower()
```

- [ ] **Step 2: Add queue contracts**

Use reasons `unfinished_attempt`, `resume_lesson`, `due_review`, `needs_transfer`, and `continue_path`. Each item carries `thread_id`, `path_id`, `objective_id`, `activity`, `reason`, `reason_text`, `priority`, and `due_at`.

```python
class LearningQueueItem(BaseModel):
    thread_id: str = ""
    path_id: str = ""
    objective_id: str = ""
    activity: dict[str, Any] = Field(default_factory=dict)
    reason: LearningQueueReason
    reason_text: str
    priority: int
    due_at: float | None = None
```

- [ ] **Step 3: Implement stable ranking**

Sort by `(priority, due_at or inf, thread_id, path_id)`. Assign priorities:

```python
UNFINISHED_ATTEMPT = 0
RESUME_LESSON = 10
DUE_REVIEW = 20
NEEDS_TRANSFER = 30
CONTINUE_PATH = 40
```

```python
def _rank(item: LearningQueueItem) -> tuple[float, float, str, str]:
    return (float(item.priority), item.due_at or float("inf"), item.thread_id, item.path_id)


def list_items(self, *, session_id: str = "", limit: int = 10, now: float | None = None) -> list[LearningQueueItem]:
    candidates = [*self._unfinished(session_id), *self._threads(session_id), *self._reviews(now), *self._paths()]
    deduped = keep_best_identity(candidates, key=_rank)
    return sorted(deduped, key=_rank)[: max(0, limit)]
```

Merge duplicates by keeping the lowest-priority-number item. Do not mutate threads, paths, or reviews while listing.

- [ ] **Step 4: Run queue tests**

Run: `pytest tests/learning/coordinator/test_learning_queue.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit the queue**

```bash
git add deeptutor/learning/queue.py deeptutor/learning/coordinator/models.py tests/learning/coordinator/test_learning_queue.py
git commit -m "feat(learning): project the next learning action"
```

## Plan 2 verification

- [ ] Run: `pytest deeptutor/learning/tests tests/learning/coordinator -q`
- [ ] Run: `pytest tests/runtime/test_orchestrator.py tests/services/session/test_capability_routing.py -q`
- [ ] Run: `ruff check deeptutor/learning tests/learning/coordinator`
- [ ] Run: `git diff --check HEAD~4..HEAD`
- [ ] Create a legacy `LearningProgress` fixture without new fields and verify its map and next action are byte-for-byte unchanged.
- [ ] Confirm evidence removal leaves an audit row and recalculates only the bound objective.
- [ ] Confirm evidence append/removal retries do not duplicate audit rows and long responses retain a resolvable `chat-turn:<turn_id>:user` reference.
- [ ] Confirm `git status --short` contains no unexpected files before starting Plan 3.
