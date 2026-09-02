# Learning Coordinator Experience and Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the coordinator on for opted-in learners, present the approved start/path/lesson experience, support inspection and recovery, and gate release on teaching-quality evaluation.

**Architecture:** A chat-loop extension translates the prepared activity into teaching instructions and exposes narrow tools for path drafts and structured assessment. Active runtime mode may change a default chat turn's capability, while explicit capability and workspace choices remain untouched. FastAPI endpoints expose queue, thread, evidence, removal, and atomic path approval; the Next.js workspace consumes those typed contracts and keeps advanced detail collapsed by default.

**Tech Stack:** Python 3.11-3.14, Node.js 22 LTS, FastAPI, Pydantic v2, existing chat-loop extensions and tool registry, TypeScript, React, Next.js, Vitest, Testing Library, Playwright, pytest.

**Spec:** `docs/superpowers/specs/2026-09-02-learning-coordinator-design.md`

## Global Constraints

- Complete Plans 1 and 2 first.
- The ordinary `chat` path remains available and is the fallback for every missing optional capability.
- Deployment mode is the rollout ceiling; active teaching changes require the current learner's saved or per-turn opt-in.
- Explicit capability, course, mastery, reading, and selection-tutor contexts cannot be auto-routed elsewhere.
- A broad path stays draft-only until an authenticated approval request succeeds.
- “Explain directly” sets help level 4 for the next activity and never counts as independent evidence.
- Path and evidence detail stay collapsed until the learner opens them.
- The UI must work at 320 CSS pixels without horizontal scrolling.
- Every new English string requires a Chinese counterpart and must pass the existing i18n checks.
- No model grader may certify release without human review.

---

## File map

Create:

- `deeptutor/capabilities/learning_coordinator/__init__.py`: package exports.
- `deeptutor/capabilities/learning_coordinator/capability.py`: loop extension and prompt block.
- `deeptutor/capabilities/learning_coordinator/tools.py`: path-draft and assessment-reporting tools.
- `deeptutor/capabilities/learning_coordinator/prompts/en/learning_coordinator.yaml`: English teaching policy.
- `deeptutor/capabilities/learning_coordinator/prompts/zh/learning_coordinator.yaml`: Chinese teaching policy.
- `deeptutor/api/routers/learning_coordinator.py`: queue/thread/evidence endpoints.
- `web/features/settings/sections/LearningSettingsSection.tsx`: personal beta opt-in.
- `tests/capabilities/test_learning_coordinator_capability.py`: activation, prompt, and tool tests.
- `tests/api/test_learning_coordinator_router.py`: authorization and endpoint tests.
- `web/lib/learning-coordinator-api.ts`: frontend API types and calls.
- `web/features/learning/components/LearningHome.tsx`: start and continue surface.
- `web/features/learning/components/LearningPathProposal.tsx`: editable approval surface.
- `web/features/learning/components/LearningActivityPanel.tsx`: rationale, sources, evidence, and teaching controls.
- `web/features/learning/hooks/useLearningQueue.ts`: queue loading and refresh.
- `web/features/learning/model.ts`: UI state and metadata parsers.
- `web/tests/learning-coordinator-api.test.ts`: transport tests.
- `web/tests/learning-coordinator-model.test.ts`: metadata parsing tests.
- `web/tests/learning-settings-page.test.ts`: personal opt-in rendering and persistence coverage.
- `web/tests/integration/learning-home.spec.tsx`: component integration.
- `web/tests/e2e/learning-coordinator.audit.ts`: critical learner flow.
- `evals/learning_coordinator/cases.jsonl`: versioned teaching scenarios.
- `evals/learning_coordinator/rubric.yaml`: human review rubric.
- `scripts/eval_learning_coordinator.py`: paired baseline runner and report writer.
- `tests/evals/test_learning_coordinator_eval.py`: fixture and scoring tests.

Modify:

- `deeptutor/capabilities/registry.py`: register the loop extension.
- `deeptutor/services/session/turns/request_preparer.py`: enable active route selection.
- `deeptutor/services/session/turns/executor.py`: pass the structured result to `finish` and expose decision metadata.
- `deeptutor/api/main.py`: mount authenticated coordinator router.
- `deeptutor/api/routers/mastery_path.py`: share path materialization helper with atomic approval.
- `deeptutor/learning/topic_generation.py`: pure confirmed-topic materialization.
- `deeptutor/learning/storage.py`: atomic thread-to-path approval transaction.
- `deeptutor/services/config/runtime_settings.py`: permit active mode already defined in Plan 1.
- `deeptutor/services/settings/interface_settings.py`: personal coordinator opt-in default and accessor.
- `deeptutor/api/routers/settings.py`: authenticated UI-settings contract for the opt-in.
- `deeptutor/core/turn_request.py`: typed resumable learning-thread binding.
- `tests/api/test_settings_router.py`: authenticated/public setting boundary.
- `tests/multi_user/test_ui_language_scoping.py`: per-user opt-in isolation.
- `web/features/settings/store/SettingsStore.tsx`: load and persist the opt-in.
- `web/features/settings/store/UiSettingsProvider.tsx`: expose the personal setting slice.
- `web/features/settings/sections/ChatSettingsSection.tsx`: mount the learning settings leaf.
- `web/features/settings/navigation/settings-nav.ts`: add the learning leaf and storage label.
- `web/features/chat/components/ChatWorkspace.tsx`: render the learning entry and activity side panel.
- `web/components/chat/home/ChatComposer.tsx`: replace mode-first copy with the approved learning prompt when active.
- `web/features/chat/model/start-turn.ts`: expose coordinator opt-in and learning action fields.
- `web/features/chat/controllers/buildStartTurnInput.ts`: serialize typed coordinator inputs.
- `web/contracts/schema/openapi.json`: regenerated API schema.
- `web/contracts/schema/turn-protocol.json`: regenerated turn schema.
- `web/contracts/generated/api.ts`: regenerated API types.
- `web/contracts/generated/turn-protocol.ts`: regenerated turn types.
- `web/locales/en/app.json`: English learning-flow copy.
- `web/locales/zh/app.json`: matching Chinese copy.
- `web/tests/integration/chat-workspace.spec.tsx`: existing chat regression coverage.
- `web/tests/settings-context-ui-sync.test.ts`: exact boolean persistence payload.
- `web/tests/settings-provider-slices.test.ts`: learning setting slice coverage.

## Task 1: Teaching loop extension and structured tools

**Files:**

- Create: `deeptutor/capabilities/learning_coordinator/__init__.py`
- Create: `deeptutor/capabilities/learning_coordinator/capability.py`
- Create: `deeptutor/capabilities/learning_coordinator/tools.py`
- Create: `deeptutor/capabilities/learning_coordinator/prompts/en/learning_coordinator.yaml`
- Create: `deeptutor/capabilities/learning_coordinator/prompts/zh/learning_coordinator.yaml`
- Modify: `deeptutor/capabilities/registry.py`
- Test: `tests/capabilities/test_learning_coordinator_capability.py`

**Interfaces:**

- Consumes: decision dict stored at `context.extension("learning_coordinator")["decision"]`.
- Produces: `LearningCoordinatorLoopCapability`, active only for validated coordinator decisions.
- Produces tools: `learning_path_draft` and `learning_report_assessment`.
- Produces result dict at `context.extension("learning_coordinator")["result"]`.

- [ ] **Step 1: Write failing activation and ownership tests**

```python
def test_extension_is_inactive_without_decision() -> None:
    assert LearningCoordinatorLoopCapability().is_active(UnifiedContext()) is False


def test_extension_activates_for_valid_decision() -> None:
    context = UnifiedContext(active_capability="chat")
    context.extension("learning_coordinator")["decision"] = lesson_decision.model_dump(mode="json")
    extension = LearningCoordinatorLoopCapability()
    assert extension.is_active(context) is True
    assert set(extension.owned_tools) == {
        "learning_path_draft",
        "learning_report_assessment",
    }


def test_prompt_requires_direct_answer_at_help_four() -> None:
    context = context_with_decision(help_level=4)
    block = LearningCoordinatorLoopCapability().system_block(context, language="en", prompts={})
    assert "give the complete answer in this turn" in block.content.lower()
```

- [ ] **Step 2: Write failing tool tests**

```python
def test_assessment_tool_exposes_no_mastery_parameter() -> None:
    names = {parameter.name for parameter in LearningReportAssessmentTool().definition.parameters}
    assert "mastery" not in names
    assert names == {"outcome", "rubric", "cited_evidence", "uncertainty"}


@pytest.mark.asyncio
async def test_path_draft_persists_draft_without_path() -> None:
    result = await LearningPathDraftTool().execute(
        _thread_id="thread-1",
        _goal="Learn thermodynamics",
        _language="en",
        _sources=[],
    )
    assert result.success is True
    assert result.metadata["proposal"]["modules"]
    assert LearningStore().load(result.metadata["proposal"].get("path_id", "")) is None


@pytest.mark.asyncio
async def test_path_draft_rejects_non_path_decision() -> None:
    result = await LearningPathDraftTool().execute(
        _scope="lesson",
        _thread_id="thread-1",
        _goal="Understand entropy",
        _language="en",
        _sources=[],
    )
    assert result.success is False
```

- [ ] **Step 3: Run tests and verify missing package failures**

Run: `pytest tests/capabilities/test_learning_coordinator_capability.py -q`

Expected: collection fails for missing extension and tools.

- [ ] **Step 4: Implement loop activation and prompt assembly**

Follow `CourseStudyLoopCapability`'s isolated-instance pattern. `is_active` parses `LearningDecision`; invalid data returns false and logs a warning. `system_block` combines the localized policy with the exact activity, help level, source policy, learner action, assessment method, and next action. The policy must say:

- ask for the planned learner action before giving more explanation unless help level is 4;
- provide the complete answer immediately at help level 4;
- call `learning_report_assessment` only when the learner supplied assessable work;
- call `learning_path_draft` for a path proposal and never call `mastery_build` before approval;
- never state a mastery score.

```python
def system_block(self, context: UnifiedContext, *, language: str, prompts: dict[str, Any]) -> PromptBlock | None:
    decision = self._decision(context)
    if decision is None:
        return None
    policy = load_learning_prompt(language)
    activity = decision.activity
    content = policy.format(
        activity_kind=activity.kind.value,
        objective=activity.objective,
        learner_action=activity.learner_action,
        help_level=activity.help_level,
        source_policy=decision.source_policy.value,
        assessment_method=activity.assessment_method,
        next_action=activity.next_action,
    )
    return PromptBlock("learning_coordinator", content)
```

- [ ] **Step 5: Implement tools with hidden runtime arguments**

`LearningPathDraftTool` accepts no model-authored scope, thread ID, source body, or path ID. `augment_kwargs` injects `_scope`, `_thread_id`, `_goal`, `_language`, and a bounded source list from the turn context. The tool rejects any scope except `path`, calls `generate_topic_draft`, stores the proposal in `LearningThread.next_activity`, and returns it as `ToolResult.metadata["proposal"]` without creating `LearningProgress`.

`LearningReportAssessmentTool` accepts only `outcome`, `rubric`, `cited_evidence`, and `uncertainty`. `augment_kwargs` injects the server-owned `_scope` and a `_record_result` callback that places the raw result in the coordinator extension namespace. It rejects answer/path scopes, because only a lesson attempt can be assessed. Unknown kwargs fail through the tool schema.

```python
def augment_kwargs(self, tool_name: str, kwargs: dict[str, Any], context: UnifiedContext) -> dict[str, Any]:
    decision = self._decision(context)
    if decision is None:
        return kwargs
    if tool_name == "learning_report_assessment":
        return {
            **kwargs,
            "_scope": decision.scope.value,
            "_record_result": lambda value: context.extension(self.name).__setitem__("result", value),
        }
    if tool_name == "learning_path_draft":
        return {
            **kwargs,
            "_scope": decision.scope.value,
            "_thread_id": decision.thread_id,
            "_goal": decision.goal,
            "_language": context.language,
            "_sources": bounded_topic_sources(context),
        }
    return kwargs
```

- [ ] **Step 6: Register the loop extension**

Add:

```python
LoopCapabilitySpec(
    "learning_coordinator",
    "deeptutor.capabilities.learning_coordinator.capability:LearningCoordinatorLoopCapability",
),
```

to `BUILTIN_LOOP_CAPABILITY_SPECS`. Keep instances turn-scoped.

- [ ] **Step 7: Run capability tests and registry regressions**

Run: `pytest tests/capabilities/test_learning_coordinator_capability.py tests/capabilities/test_loop_registry.py tests/capabilities/test_status_i18n_consistency.py -q`

Expected: all tests pass.

- [ ] **Step 8: Commit the teaching bridge**

```bash
git add deeptutor/capabilities/learning_coordinator deeptutor/capabilities/registry.py tests/capabilities/test_learning_coordinator_capability.py
git commit -m "feat(learning): guide chat with coordinator activities"
```

## Task 2: Active routing and authenticated learning API

**Files:**

- Modify: `deeptutor/services/session/turns/request_preparer.py`
- Modify: `deeptutor/services/session/turns/executor.py`
- Modify: `deeptutor/core/turn_request.py`
- Modify: `deeptutor/services/settings/interface_settings.py`
- Modify: `deeptutor/api/routers/settings.py`
- Create: `deeptutor/api/routers/learning_coordinator.py`
- Modify: `deeptutor/api/routers/mastery_path.py`
- Modify: `deeptutor/learning/topic_generation.py`
- Modify: `deeptutor/learning/storage.py`
- Modify: `deeptutor/api/main.py`
- Create: `tests/api/test_learning_coordinator_router.py`
- Modify: `tests/api/test_settings_router.py`
- Modify: `tests/multi_user/test_ui_language_scoping.py`
- Modify: `tests/learning/coordinator/test_shadow_runtime.py`

**Interfaces:**

- Produces active mode behavior: default chat may use `LearningDecision.route`; explicit and workspace-bound turns do not.
- Produces turn fields `learning_coordinator: bool | None` and `learning_thread_id: str | None`, with the ID limited to 128 characters.
- Produces personal UI setting `learning_coordinator_enabled: bool`, default `false`; it is authenticated and never included in `PRESESSION_UI_FIELDS`.
- Produces `GET /api/learning/queue?session_id=`.
- Produces `GET /api/learning/threads/{thread_id}`.
- Produces `GET /api/learning/threads/{thread_id}/evidence`.
- Produces `DELETE /api/learning/evidence/{evidence_id}`.
- Produces `POST /api/learning/threads/{thread_id}/approve-path`.
- Produces `POST /api/learning/threads/{thread_id}/help` with `help_level: 0..4`.

- [ ] **Step 1: Write failing active-route tests**

```python
@pytest.mark.asyncio
async def test_active_mode_routes_default_chat() -> None:
    captured = await run_turn(
        mode="active",
        saved_opt_in=True,
        decision=lesson_decision(route="mastery_path"),
    )
    assert captured["active_capability"] == "mastery_path"
    assert captured["requested_capability"] == "chat"


@pytest.mark.asyncio
async def test_active_mode_does_not_override_explicit_capability() -> None:
    captured = await run_turn(mode="active", requested_capability="deep_solve")
    assert captured["active_capability"] == "deep_solve"


@pytest.mark.asyncio
async def test_active_mode_does_not_override_bound_reading_workspace() -> None:
    captured = await run_turn(
        mode="active",
        requested_capability="immersive_reading",
        workspace_mode="reading",
    )
    assert captured["active_capability"] == "immersive_reading"


@pytest.mark.asyncio
async def test_active_deployment_keeps_unopted_user_on_chat() -> None:
    captured = await run_turn(mode="active", saved_opt_in=False)
    assert captured["active_capability"] == "chat"
    assert captured["learning_decision"] is None


@pytest.mark.asyncio
async def test_per_turn_false_overrides_saved_opt_in() -> None:
    captured = await run_turn(
        mode="active",
        saved_opt_in=True,
        learning_coordinator=False,
    )
    assert captured["active_capability"] == "chat"
```

- [ ] **Step 2: Write failing endpoint tests**

Cover unauthenticated rejection, per-user store isolation, queue response shape, thread not found, evidence removal idempotency, help-level validation, and path approval. The approval test must assert both path creation and thread binding after one request, then repeat the same request and get the same path ID.

Also validate the turn contract:

```python
request = TurnRequest(content="Continue", learning_thread_id="thread-1")
assert request.to_payload()["learning_thread_id"] == "thread-1"
assert TurnRequest(content="Learn", learning_coordinator=True).to_payload()[
    "learning_coordinator"
] is True
with pytest.raises(ValidationError):
    TurnRequest(content="Continue", learning_thread_id="x" * 129)
```

Add settings tests that prove the fresh per-user default is false, authenticated `PUT /api/settings/ui` persists the boolean, two users read different values, and public `GET /api/settings/ui` still returns exactly `PRESESSION_UI_FIELDS` without the coordinator flag.

- [ ] **Step 3: Add the authenticated personal opt-in**

Add `"learning_coordinator_enabled": False` to `DEFAULT_UI_SETTINGS` and:

```python
def get_learning_coordinator_enabled() -> bool:
    return get_ui_settings().get("learning_coordinator_enabled") is True
```

Add `learning_coordinator_enabled: bool = False` to `UISettings` and `learning_coordinator_enabled: bool | None = None` to `UISettingsUpdate`. Keep `PRESESSION_UI_FIELDS = ("theme", "language", "response_language")` unchanged: login and static pages do not need a teaching-policy preference. The authenticated `GET /api/settings` response already returns the defaults-merged `ui` object, and `PUT /api/settings/ui` persists the field through the existing atomic partial update.

- [ ] **Step 4: Enable active route selection**

Resolve the effective coordinator mode separately from the existing `auto_route` quiz policy. The system `learning_coordinator_mode` is the rollout ceiling: `off` disables preparation, `shadow` records decisions without changing routing, and `active` changes routing only when `learning_coordinator` is true for this turn or, when omitted, `get_learning_coordinator_enabled()` is true for the authenticated user. An explicit per-turn false always opts out. Never use or change `auto_route` for this decision. Preserve `route_explicit_quiz_request` precedence: if it returns a route, use that route and skip the learning coordinator for this turn. Run model-assisted classification only after validating and resolving the user's `llm_selection`; a non-admin request must never use the administrator's fallback model.

```python
rollout_mode = load_system_settings().get("learning_coordinator_mode", "off")
per_turn_opt_in = payload.get("learning_coordinator")
user_opted_in = (
    get_learning_coordinator_enabled()
    if per_turn_opt_in is None
    else per_turn_opt_in is True
)
effective_mode = (
    "shadow"
    if rollout_mode == "shadow" and per_turn_opt_in is not False
    else "active"
    if rollout_mode == "active" and user_opted_in
    else "off"
)
```

In `TurnRequestPreparer`, coordinator eligibility is:

```python
eligible = (
    requested_capability == "chat"
    and not requested_course_id
    and not payload.get("mastery_path_id")
    and not workspace_mode
    and not payload.get("selection_tutor_context")
)
```

In `active` mode, use `decision.route` only if the registry contains it; otherwise use `chat`. Re-run capability-config validation and tool filtering against the selected route. Keep the persisted session preference as the requested capability. Store both values in decision metadata.

Add `learning_coordinator: bool | None = None` and `learning_thread_id: str | None = Field(default=None, max_length=128)` to `TurnRequest`. For `lesson` or `path`, create or resume a `LearningThread` before turn creation and populate the existing `LearningDecision.thread_id` field. A path thread starts `draft`; a lesson thread starts `active`. Persist the prepared activity as `next_activity` before launching the capability so cancellation can resume it without replaying the whole lesson; a cancelled or failed turn records no evidence. Use a client-supplied `learning_thread_id` only after store ownership validation. When a validated prior thread exists, load its server-owned previous help level, last evidence outcome, and current activity identity into `payload["learning_state"]`. Mark `repeated_request` when normalized text matches the prior request or contains the paired English/Chinese “again” or “still don't understand” phrases; don't ask a model to decide this flag.

- [ ] **Step 5: Finalize from the loop extension result**

In `executor.py`, pass `context.extension("learning_coordinator").get("result")` through `CapabilityLearningResult.model_validate`. Missing result means no evidence, not failure. Preserve the learner's raw message separately from workspace context and use only that raw text for quoted-evidence validation.

```python
raw_result = context.extension("learning_coordinator").get("result")
if isinstance(raw_result, dict):
    await learning_coordinator.finish(
        decision,
        CapabilityLearningResult.model_validate(raw_result),
        session_id=session_id,
        turn_id=turn_id,
        learner_response=raw_user_content,
        allowed_source_refs=trusted_source_ids_from_turn(
            source_index,
            attachment_records,
            context.capability_output.event_metadata,
        ),
    )
```

- [ ] **Step 6: Extract pure materialization and atomic approval**

Extract the validation/building portion of the current `/mastery-paths/topics` body into a pure helper:

```python
def materialize_topic_draft(
    *,
    path_id: str,
    name: str,
    goal: str,
    description: str,
    emoji: str,
    sources: list[TopicSource],
    modules: list[dict[str, Any]],
) -> ConfirmedTopicDraft:
    ...
```

`ConfirmedTopicDraft` contains the validated name, `LearningModule` rows, `TopicMetadata`, and sources but performs no I/O. Keep the existing endpoint response unchanged by passing this materialization to `LearningService.create_topic`.

Add `LearningStore.approve_learning_thread_path(thread_id, materialized)` as the only atomic approval writer. It opens one `BEGIN IMMEDIATE` connection, validates that the owned thread is still `draft`, returns the existing path on replay, inserts the mastery path/topic/event rows using the store's internal connection-level helpers, binds `thread.path_id`, moves the thread to `active`, and appends `thread.path_approved` before one commit. Any exception rolls back both the path and thread update. Do not call `LearningService.create_topic` or open a nested transaction from inside this method.

Use `path_id = f"topic_{uuid.uuid5(uuid.NAMESPACE_URL, thread.thread_id).hex}"` so concurrent retries target one identity. Under `BEGIN IMMEDIATE`, claim the draft with `UPDATE learning_threads SET path_id = ?, status = 'active' WHERE thread_id = ? AND path_id = '' AND status = 'draft'`; a zero-row update reloads and returns the path another request created. Add a two-thread barrier test: both approval requests must return the same path ID, only one path/topic pair exists, and only one `thread.path_approved` audit event exists.

- [ ] **Step 7: Implement queue, thread, evidence, removal, and help endpoints**

Return Pydantic response models, not raw database rows. `DELETE` records `removed_at`, recalculates the bound objective, and returns the revised evidence list plus mastery revision when applicable. The help endpoint may only increase the current activity's help level unless `help_level=0` starts a new activity.

```python
@router.get("/queue", response_model=LearningQueueResponse)
async def get_queue(session_id: str = "") -> LearningQueueResponse:
    items = await asyncio.to_thread(LearningQueueService().list_items, session_id=session_id)
    return LearningQueueResponse(items=items)


@router.delete("/evidence/{evidence_id}", response_model=EvidenceListResponse)
async def delete_evidence(evidence_id: str) -> EvidenceListResponse:
    service = LearningEvidenceApplicationService()
    return await asyncio.to_thread(service.remove_and_recalculate, evidence_id)
```

- [ ] **Step 8: Mount the authenticated router**

Import `learning_coordinator` beside other API routers and mount:

```python
app.include_router(
    learning_coordinator.router,
    prefix="/api/learning",
    tags=["learning"],
    dependencies=_auth,
)
```

- [ ] **Step 9: Run backend integration tests**

Run: `pytest tests/api/test_learning_coordinator_router.py tests/api/test_settings_router.py tests/multi_user/test_ui_language_scoping.py tests/learning/coordinator tests/capabilities/test_learning_coordinator_capability.py tests/services/session/test_capability_routing.py -q`

Expected: all tests pass.

- [ ] **Step 10: Commit active backend behavior**

Before committing, regenerate backend-owned schemas:

```bash
python scripts/export_frontend_contracts.py
```

```bash
git add deeptutor/core/turn_request.py deeptutor/services/settings/interface_settings.py deeptutor/api/routers/settings.py deeptutor/services/session/turns/request_preparer.py deeptutor/services/session/turns/executor.py deeptutor/api/routers/learning_coordinator.py deeptutor/api/routers/mastery_path.py deeptutor/learning/topic_generation.py deeptutor/learning/storage.py deeptutor/api/main.py tests/api/test_learning_coordinator_router.py tests/api/test_settings_router.py tests/multi_user/test_ui_language_scoping.py tests/learning/coordinator/test_shadow_runtime.py web/contracts/schema/openapi.json web/contracts/schema/turn-protocol.json
git commit -m "feat(learning): enable coordinated learning turns"
```

## Task 3: Frontend contracts and learning surfaces

**Files:**

- Create: `web/lib/learning-coordinator-api.ts`
- Create: `web/features/learning/model.ts`
- Create: `web/features/learning/hooks/useLearningQueue.ts`
- Create: `web/features/learning/components/LearningHome.tsx`
- Create: `web/features/learning/components/LearningPathProposal.tsx`
- Create: `web/features/learning/components/LearningActivityPanel.tsx`
- Create: `web/tests/learning-coordinator-api.test.ts`
- Create: `web/tests/learning-coordinator-model.test.ts`
- Create: `web/tests/integration/learning-home.spec.tsx`
- Modify: `web/contracts/generated/api.ts`
- Modify: `web/contracts/generated/turn-protocol.ts`

**Interfaces:**

- Consumes: Task 2 REST endpoints, SESSION/DONE `learning_decision` metadata, and `learning_path_draft` tool-result proposal metadata.
- Produces: `LearningQueueItem`, `LearningThread`, `LearningEvidence`, `LearningPathDraft`, and `LearningDecision` TypeScript types.
- Produces: `parseLearningDecision(value: unknown): LearningDecision | null`, `parseLearningPathProposal(value: unknown): LearningPathDraft | null`, and selectors that scan `MessageItem.events` from newest to oldest.
- Produces: `useLearningQueue(sessionId?: string)` with `items`, `loading`, `error`, and `refresh`.

- [ ] **Step 1: Write failing transport tests**

```typescript
it("encodes session id when loading the queue", async () => {
  mockFetchJson({ items: [] });
  await fetchLearningQueue("session / one");
  expect(fetch).toHaveBeenCalledWith(
    expect.stringContaining("session_id=session+%2F+one"),
    expect.anything(),
  );
});

it("uses DELETE for evidence removal", async () => {
  mockFetchJson({ evidence: [] });
  await removeLearningEvidence("ev one");
  expect(fetch).toHaveBeenCalledWith(
    expect.stringContaining("ev%20one"),
    expect.objectContaining({ method: "DELETE" }),
  );
});
```

- [ ] **Step 2: Write failing parser tests**

Test a valid decision, unknown scope, missing activity, help level 5, malformed metadata, a valid tool-result proposal, and a proposal with an empty module. Invalid input must return `null`, not throw in the render path.

- [ ] **Step 3: Implement API client and defensive parser**

Use `apiFetch`, `apiUrl`, `encodeURIComponent`, and `URLSearchParams` consistently with current clients. Keep wire names in `snake_case`; components map them to display labels. Reject any decision whose route, reason, objective, or learner action exceeds the parser's length cap.

```typescript
export async function fetchLearningQueue(sessionId = ""): Promise<LearningQueueItem[]> {
  const query = new URLSearchParams();
  if (sessionId) query.set("session_id", sessionId);
  const response = await apiFetch(apiUrl(`/api/learning/queue?${query}`));
  if (!response.ok) throw new Error(`Failed to load learning queue: ${response.status}`);
  return ((await response.json()) as { items: LearningQueueItem[] }).items;
}
```

Regenerate TypeScript contracts before importing the new response types:

```bash
cd web && npm run contracts:generate
```

- [ ] **Step 4: Implement `LearningHome`**

Render the approved prompt, one “Continue learning” item, and due-review count. The component accepts callbacks; it does not own chat transport. Loading uses a stable skeleton, an empty queue hides the cards, and an error leaves the composer usable.

```tsx
export function LearningHome({ items, loading, onContinue }: LearningHomeProps) {
  const next = items[0];
  return (
    <section aria-labelledby="learning-home-title">
      <h1 id="learning-home-title">{t("What do you want to understand?")}</h1>
      {loading ? <LearningQueueSkeleton /> : next ? <ContinueLearningCard item={next} onContinue={onContinue} /> : null}
    </section>
  );
}
```

- [ ] **Step 5: Implement `LearningPathProposal`**

Use controlled inputs for goal, starting point, sources, teaching preferences, and ordered modules. Require at least one module and one objective per module before enabling “Approve path and begin.” Approval sends the current edited draft, disables duplicate submission, and routes to `/mastery/{pathId}` only after success.

```tsx
const valid = draft.modules.length > 0 && draft.modules.every(
  (module) => module.knowledge_points.length > 0,
);
<button disabled={!valid || approving} onClick={() => void approvePath(threadId, draft)}>
  {t("Approve path and begin")}
</button>
```

- [ ] **Step 6: Implement `LearningActivityPanel`**

Show the active activity and keep `Why this next?`, evidence, and sources in collapsed native `details` elements. Controls call typed callbacks for hint, direct answer, visual emphasis, rigor, and pacing. “Explain directly” must display a short notice that this attempt will not count as independent evidence.

```tsx
<details><summary>{t("Why this next?")}</summary><p>{decision.reason}</p></details>
<details><summary>{t("Learning evidence")}</summary><EvidenceList records={evidence} /></details>
<button onClick={() => onHelp(4)}>{t("Explain directly")}</button>
{pendingHelpLevel === 4 ? <p role="status">{t("This attempt will not count as independent evidence.")}</p> : null}
```

- [ ] **Step 7: Run frontend unit and integration tests**

Run: `cd web && npm run test:unit -- learning-coordinator-api learning-coordinator-model learning-home`

Expected: all selected tests pass.

- [ ] **Step 8: Commit frontend components**

```bash
git add web/contracts/generated/api.ts web/contracts/generated/turn-protocol.ts web/lib/learning-coordinator-api.ts web/features/learning web/tests/learning-coordinator-api.test.ts web/tests/learning-coordinator-model.test.ts web/tests/integration/learning-home.spec.tsx
git commit -m "feat(web): add coordinated learning surfaces"
```

## Task 4: Integrate the approved learner flow

**Files:**

- Modify: `web/features/chat/components/ChatWorkspace.tsx`
- Modify: `web/components/chat/home/ChatComposer.tsx`
- Modify: `web/features/chat/model/start-turn.ts`
- Modify: `web/features/chat/controllers/buildStartTurnInput.ts`
- Create: `web/features/settings/sections/LearningSettingsSection.tsx`
- Modify: `web/features/settings/store/SettingsStore.tsx`
- Modify: `web/features/settings/store/UiSettingsProvider.tsx`
- Modify: `web/features/settings/sections/ChatSettingsSection.tsx`
- Modify: `web/features/settings/navigation/settings-nav.ts`
- Modify: `web/locales/en/app.json`
- Modify: `web/locales/zh/app.json`
- Modify: `web/tests/integration/chat-workspace.spec.tsx`
- Create: `web/tests/learning-settings-page.test.ts`
- Modify: `web/tests/settings-context-ui-sync.test.ts`
- Modify: `web/tests/settings-provider-slices.test.ts`
- Create: `web/tests/e2e/learning-coordinator.audit.ts`

**Interfaces:**

- Consumes: components and hook from Task 3.
- Produces: saved `learningCoordinatorEnabled` state and a personal Settings > Chat > Learning toggle.
- Produces: dedicated start-turn fields `learningCoordinator?: boolean` and `learningThreadId?: string | null`; existing `autoRoute` behavior is unchanged.
- Produces: browser flow from start to proposal approval to active lesson.

- [ ] **Step 1: Write failing workspace integration tests**

Test these visible behaviors:

```typescript
it("asks what the learner wants to understand on an opted-in empty chat", async () => {
  mockLearningCoordinatorEnabled(true);
  render(<ChatWorkspace />);
  expect(screen.getByText("What do you want to understand?")).toBeVisible();
});

it("preserves the ordinary home when the learner has not opted in", async () => {
  mockLearningCoordinatorEnabled(false);
  render(<ChatWorkspace />);
  expect(screen.queryByText("What do you want to understand?")).toBeNull();
  expect(screen.getByRole("textbox")).toBeEnabled();
});

it("keeps the composer usable when queue loading fails", async () => {
  mockQueueFailure();
  render(<ChatWorkspace />);
  expect(screen.getByRole("textbox")).toBeEnabled();
});

it("shows a path proposal for path-scope metadata", async () => {
  renderWorkspaceWithDecision(pathDecisionFixture);
  expect(await screen.findByRole("heading", { name: /proposed learning path/i })).toBeVisible();
});
```

- [ ] **Step 2: Write failing personal-setting tests**

Add tests that assert:

```typescript
test("settings-context: persists only the learning coordinator boolean", async () => {
  let capturedInit: RequestInit | undefined;
  await persistUiSettingsPatch(
    { learning_coordinator_enabled: true },
    async (_input, init) => {
      capturedInit = init;
      return { ok: true } as Response;
    },
  );
  assert.deepEqual(JSON.parse(String(capturedInit?.body)), {
    learning_coordinator_enabled: true,
  });
});

test("learning settings: toggle reflects and updates the personal setting", () => {
  const source = readLearningSettingsSource();
  assert.match(source, /learningCoordinatorEnabled/);
  assert.match(source, /updateLearningCoordinatorEnabled/);
});
```

Extend `settings-provider-slices.test.ts` to require both values in `UiSettingsProvider` and confirm the learning page uses `useUiSettings()`, not the full catalog settings surface.

- [ ] **Step 3: Implement the saved learner opt-in**

Extend `UiSettings` with `learning_coordinator_enabled: boolean`. Add `learningCoordinatorEnabled`, `updateLearningCoordinatorEnabled`, state initialization to false, authenticated settings-load hydration, and an immediate atomic partial update to `SettingsContextValue`:

```typescript
const updateLearningCoordinatorEnabled = useCallback(async (next: boolean) => {
  const previous = learningCoordinatorEnabled;
  setLearningCoordinatorEnabled(next);
  try {
    await persistUiSettingsPatch({ learning_coordinator_enabled: next });
  } catch (error) {
    setLearningCoordinatorEnabled(previous);
    throw error;
  }
}, [learningCoordinatorEnabled]);
```

Expose those two values through `UiSettingsProvider`. Create `LearningSettingsSection` with one `Toggle`, beta explanation, and a note that explicit modes still win. Add a non-admin `learning` leaf before `tools` in `CHAT_CHILDREN`, mount it in `CHAT_SECTIONS`, and map both `/settings#learning` and `learning` to `data/user/settings/interface.json` in `STORAGE_PATHS`.

- [ ] **Step 4: Add typed start-turn options**

Extend `StartTurnInput` with `learningCoordinator?: boolean | null` and `learningThreadId?: string | null`. In `buildStartTurnInput`, emit the dedicated `learning_coordinator` and `learning_thread_id` fields while leaving `auto_route` untouched. Add both snake-case names to `RUNTIME_ONLY_CONFIG_KEYS` so they cannot leak through capability config, and test that a 129-character thread ID is rejected by the generated turn parser. Explicit capability selections send `learningCoordinator: false`; default chat sends `learningCoordinatorEnabled`.

```typescript
return buildStartTurn({
  ...existingFields,
  auto_route: input.autoRoute ?? null,
  learning_coordinator: input.learningCoordinator ?? null,
  learning_thread_id: input.learningThreadId ?? null,
});
```

- [ ] **Step 5: Integrate empty, active, and proposal states**

In `ChatWorkspace`:

- empty chat: render `LearningHome` above the standalone composer;
- active lesson: render normal messages and `LearningActivityPanel` in the existing Activity/Viewer area;
- path decision with proposal metadata: render `LearningPathProposal` as the primary content;
- ordinary or explicitly selected capability: preserve the current layout.

Do not add a second router or chat state store.

```tsx
const learningDecision = selectLatestLearningDecision(state.messages);
const pathDraft = selectLatestLearningPathProposal(state.messages);
if (!state.hasMessages && coordinatorEnabled) {
  return <LearningHome items={learningQueue.items} loading={learningQueue.loading} onContinue={continueLearning} />;
}
if (learningDecision?.scope === "path" && pathDraft) {
  return <LearningPathProposal threadId={learningDecision.thread_id} initialDraft={pathDraft} />;
}
```

- [ ] **Step 6: Connect help and teaching controls**

Hints and direct answers call `POST /api/learning/threads/{id}/help`, then prefill and send one natural-language continuation through the existing chat adapter. Visual, rigor, and pacing controls update the thread's next activity; they do not edit mastery or global preferences.

```typescript
const requestHelp = async (level: number) => {
  await setLearningHelp(threadId, level);
  await sendMessage({
    content: level === 4 ? t("Please explain the answer directly.") : t("Give me the next hint."),
    learningCoordinator: true,
  });
};
```

- [ ] **Step 7: Add i18n strings in parity**

Add the approved copy for start, continue, due review, proposed path, approval, direct answer, rationale, sources, evidence, and teaching controls. Run parity before changing component snapshots.

```json
{
  "What do you want to understand?": "What do you want to understand?",
  "Approve path and begin": "Approve path and begin",
  "Explain directly": "Explain directly",
  "Why this next?": "Why this next?"
}
```

Add the same keys with Chinese values to `web/locales/zh/app.json`; merge into the existing JSON object rather than replacing the file.

Also add paired copy for the Settings > Chat > Learning leaf, its beta toggle, and the opt-in explanation.

- [ ] **Step 8: Add critical Playwright flow**

Mock API and turn transport to cover:

1. enter “Teach me Fourier transforms”;
2. receive a `path` decision and draft;
3. edit the goal and approve;
4. enter the first activity;
5. request a hint, then inspect evidence and sources.

At the mobile viewport, assert `document.documentElement.scrollWidth === document.documentElement.clientWidth`.

```typescript
test("broad goal becomes an editable approved path", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 760 });
  await mockPersonalSettings(page, { learning_coordinator_enabled: true });
  await page.goto("/chat");
  await page.getByRole("textbox").fill("Teach me Fourier transforms");
  await page.getByRole("button", { name: /send/i }).click();
  await page.getByRole("heading", { name: /proposed learning path/i }).waitFor();
  await page.getByLabel(/i want to be able to/i).fill("Analyze unfamiliar signals");
  await page.getByRole("button", { name: /approve path and begin/i }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    await page.evaluate(() => document.documentElement.clientWidth),
  );
});
```

- [ ] **Step 9: Run web checks**

Run: `cd web && npm run i18n:check && npm run typecheck && npm run test:node && npm run test:unit`

Expected: all checks pass.

Run: `cd web && npx playwright test tests/e2e/learning-coordinator.audit.ts --project=ui-audit`

Expected: desktop and configured mobile projects pass.

- [ ] **Step 10: Commit the integrated experience**

```bash
git add web/features/chat/components/ChatWorkspace.tsx web/components/chat/home/ChatComposer.tsx web/features/chat/model/start-turn.ts web/features/chat/controllers/buildStartTurnInput.ts web/features/settings/sections/LearningSettingsSection.tsx web/features/settings/store/SettingsStore.tsx web/features/settings/store/UiSettingsProvider.tsx web/features/settings/sections/ChatSettingsSection.tsx web/features/settings/navigation/settings-nav.ts web/locales/en/app.json web/locales/zh/app.json web/tests/integration/chat-workspace.spec.tsx web/tests/learning-settings-page.test.ts web/tests/settings-context-ui-sync.test.ts web/tests/settings-provider-slices.test.ts web/tests/e2e/learning-coordinator.audit.ts
git commit -m "feat(web): integrate adaptive learning flow"
```

## Task 5: Teaching evaluation and release gate

**Files:**

- Create: `evals/learning_coordinator/cases.jsonl`
- Create: `evals/learning_coordinator/rubric.yaml`
- Create: `scripts/eval_learning_coordinator.py`
- Create: `tests/evals/test_learning_coordinator_eval.py`
- Modify: `CONTRIBUTING.md`: document the paired teaching-evaluation command.

**Interfaces:**

- Produces: `python scripts/eval_learning_coordinator.py --mode paired --output .artifacts/learning-coordinator-eval.json`.
- Produces report fields: case ID, domain, scope, baseline result, coordinator result, contract failures, human rubric slots, latency, and token usage.
- Consumes: ordinary chat and active coordinator mode against the same request fixture.

- [ ] **Step 1: Write failing fixture-validation tests**

```python
def test_scenario_matrix_covers_required_domains_and_states() -> None:
    cases = load_cases(CASES_PATH)
    assert {case.domain for case in cases} >= {
        "mathematics",
        "physical_science",
        "programming",
        "humanities",
        "open_analysis",
    }
    assert {case.scope for case in cases} == {"answer", "lesson", "path"}
    assert any(case.stuck_signal for case in cases)
    assert any(case.direct_answer for case in cases)
    assert any(case.interrupted for case in cases)


def test_invalid_assessment_is_a_hard_contract_failure() -> None:
    result = score_contracts(case, output_with_malformed_assessment)
    assert "invalid_assessment_mutated_mastery" in result.failures
```

- [ ] **Step 2: Create the scenario matrix**

Add at least 40 cases. Every required domain gets answer, lesson, and path cases; the set also includes misconceptions, stuck signals, explicit direct-answer requests, attached-only source rules, missing tools, and interrupted sessions. Store expected contracts, not a single preferred prose answer.

```json
{"id":"math-concept-001","domain":"mathematics","scope":"lesson","prompt":"Help me understand why eigenvectors matter","expected":{"requires_approval":false,"requires_transfer":true},"stuck_signal":false,"direct_answer":false,"interrupted":false}
```

- [ ] **Step 3: Add the human rubric**

Use 0 to 4 anchored scales for factual correctness, method fit, diagnosis quality, source honesty, cognitive load, and independent transfer. Define each integer anchor. Reviewers must see randomized labels `A` and `B`, not mode names.

```yaml
dimensions:
  factual_correctness:
    0: materially wrong or unsafe
    2: mostly correct with a meaningful omission
    4: correct, bounded, and checkable
  independent_transfer:
    0: no learner action
    2: near-copy practice
    4: an independent unfamiliar application
```

- [ ] **Step 4: Implement deterministic contract scoring**

The runner validates scope, approval gate, direct-answer compliance, source IDs, evidence schema, help level, route availability, and resume state without a model judge. Any durable path created before approval or any invalid assessment that changes mastery marks the case failed.

```python
def score_contracts(case: EvalCase, result: EvalResult) -> ContractScore:
    failures: list[str] = []
    if case.scope == "path" and result.path_created_before_approval:
        failures.append("path_created_before_approval")
    if case.direct_answer and result.final_help_level != 4:
        failures.append("direct_answer_not_honored")
    if result.invalid_assessment_mutated_mastery:
        failures.append("invalid_assessment_mutated_mastery")
    return ContractScore(passed=not failures, failures=failures)
```

- [ ] **Step 5: Implement paired execution and report output**

Run baseline and coordinator turns with the same model, settings, sources, and seed where the provider supports one. Record raw output references, not secrets. Resume interrupted cases from stored turn state. Leave human rubric fields `null` until a reviewer fills them; do not auto-pass them.

```python
for case in load_cases(args.cases):
    baseline = await run_case(case, coordinator_mode="off")
    coordinated = await run_case(
        case,
        coordinator_mode="active",
        learning_coordinator=True,
    )
    report.append(build_pair(case, baseline, coordinated, randomize_labels=True))
write_redacted_report(Path(args.output), report)
```

- [ ] **Step 6: Run eval harness tests**

Run: `pytest tests/evals/test_learning_coordinator_eval.py -q`

Expected: fixture, contract, redaction, and paired-report tests pass.

- [ ] **Step 7: Run the local paired evaluation**

Run: `python scripts/eval_learning_coordinator.py --mode paired --output .artifacts/learning-coordinator-eval.json`

Expected: report generated with all deterministic contract fields populated. Provider-unavailable cases must report `blocked`, not pass.

- [ ] **Step 8: Commit the evaluation harness**

```bash
git add evals/learning_coordinator scripts/eval_learning_coordinator.py tests/evals/test_learning_coordinator_eval.py CONTRIBUTING.md
git commit -m "test(learning): add teaching quality evaluation"
```

## Plan 3 verification

- [ ] Run: `pytest deeptutor/learning/tests tests/learning/coordinator tests/capabilities/test_learning_coordinator_capability.py tests/api/test_learning_coordinator_router.py tests/api/test_settings_router.py tests/multi_user/test_ui_language_scoping.py tests/runtime/test_orchestrator.py tests/services/session/test_capability_routing.py tests/evals/test_learning_coordinator_eval.py -q`
- [ ] Run: `ruff check deeptutor/learning deeptutor/capabilities/learning_coordinator deeptutor/api/routers/learning_coordinator.py deeptutor/api/routers/settings.py deeptutor/services/settings/interface_settings.py scripts/eval_learning_coordinator.py tests/learning/coordinator tests/evals`
- [ ] Run: `cd web && npm run check:fast`
- [ ] Run: `cd web && npm run build`
- [ ] Run: `cd web && npx playwright test tests/e2e/learning-coordinator.audit.ts --project=ui-audit`
- [ ] Run a clean paired evaluation and collect human rubric entries before changing the default from `off` or opt-in `active`.
- [ ] Inspect `git diff --check`, `git status --short`, the final setting default, and all new generated contract files.
- [ ] Confirm `.superpowers/` prototype artifacts are not staged or committed.
