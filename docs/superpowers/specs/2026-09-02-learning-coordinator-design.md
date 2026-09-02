# DeepTutor Learning Coordinator Design

**Date:** 2026-09-02

**Status:** Approved in conversation; awaiting review of this written specification

**Repository baseline:** `40b2d910fb228476cbb38cb3d1a91b32eb5f250d`

## Decision

DeepTutor will add a Learning Coordinator above its existing capabilities. The coordinator will decide how much teaching structure a request needs, choose one suitable learning activity, route that activity through the current runtime, and convert the result into inspectable learner evidence.

It will not replace `ChatOrchestrator`, create a second mastery system, or turn every question into a course. Existing capabilities and stores remain the systems of record.

The default teaching behavior is guide-first. DeepTutor gives a direct answer when the learner asks for one or shows a genuine stuck signal.

## Why this change

DeepTutor already has capable parts: chat, mastery paths, courses, books, immersive reading, visualization, deep solving, memory, and spaced review. The missing piece is an educational decision layer that can answer four questions consistently:

1. Is this a quick question, a difficult concept, or a broad learning goal?
2. What should the learner do next, rather than merely read next?
3. What does the learner's response prove?
4. Which existing DeepTutor surface should carry out the activity?

Without that layer, learners must understand the product's modes before the product can teach them. The proposed design reverses that burden.

## Product goals

- A learner can type any learning request without choosing Chat, Mastery, Course, Reading, or Visualize first.
- The response matches the request's size: a concise answer, a short adaptive lesson, or an editable learning-path proposal.
- Difficult STEM work gets diagrams, derivations, worked examples, guided practice, and transfer checks when those methods fit the objective.
- Humanities and other non-STEM work gets evidence comparison, interpretation, argument testing, source criticism, and project work rather than a forced quiz format.
- DeepTutor can explain why it chose the next activity and what evidence supports a mastery judgment.

## Non-goals

- Reimplementing OpenMAIC inside DeepTutor.
- Replacing `ChatOrchestrator` or the capability registry.
- Maintaining a second copy of courses, books, reading progress, mastery, or conversation state.
- Spawning a cast of teacher personas by default. Multiple agents add cost and cognitive load without proving that the learner understands more.
- Generating a semester-sized course before the learner approves its goal, sources, and outline.
- Treating engagement, response length, or learner confidence as mastery.

## What DeepTutor should take from OpenMAIC

The comparison used OpenMAIC commit `f760f58a70e6d624a8e49dcb2f7bfbda8c1069e1` as the reference point.

OpenMAIC and DeepTutor pursue a similar product goal, but their structures differ. OpenMAIC centers on generated teaching experiences, classroom scenes, course authoring, project-based learning, and reusable teaching instructions. DeepTutor already has a stronger long-lived learning core: deterministic mastery gates, review scheduling, persistent paths, capability routing, and several mature learning surfaces.

DeepTutor should adapt four OpenMAIC ideas:

- **Typed instructional activities.** A lesson should consist of named activities with clear inputs and outcomes, not an undifferentiated stream of prose.
- **Reusable teaching recipes.** Store versioned, inspectable recipes for methods such as worked-example fading, prediction before explanation, source comparison, and project critique. These recipes constrain planning; they don't execute arbitrary code.
- **Editable course authoring.** Broad requests should produce a draft goal, source set, and ordered route that the learner can change before creation.
- **Project-based work.** Analysis and design objectives need artifacts, critique, and revision. Recall questions alone can't establish mastery there.

DeepTutor should retain its own runtime, stores, entry points, mastery policy, and review queue. That combination captures OpenMAIC's strongest teaching ideas without importing a parallel platform.

## Architecture

The Learning Coordinator wraps capability selection; it doesn't become a new all-purpose capability.

```text
Learner request
    |
    v
ChatOrchestrator
    |
    +--> LearningCoordinator.prepare(context)
    |        |-- ScopeDetector
    |        |-- TeachingStrategist
    |        `-- ActivityPlanner
    |
    +--> existing capability selected by the plan
    |        chat | mastery_path | course_study | reading
    |        deep_solve | visualize | other registered capability
    |
    +--> LearningCoordinator.finish(result)
             |-- EvidenceAdapter
             `-- LearningQueue projection
```

`ChatOrchestrator` keeps ownership of session IDs, the `StreamBus`, capability lifecycle, errors, and completion events. The coordinator receives an interface for inspecting available capabilities and returns a typed decision. It must not call the orchestrator recursively.

When the coordinator is disabled, when a client explicitly selects a capability, or when preparation fails, current routing behavior remains available. An explicit learner or client choice always wins.

### Coordinator contract

`prepare(context)` returns a `LearningDecision`:

```text
scope: answer | lesson | path
route: registered capability name
goal: normalized learner goal
objective_id: optional existing mastery objective
activity: typed ActivityPlan
reason: short learner-readable rationale
confidence: 0..1
requires_approval: boolean
source_policy: attached_only | attached_preferred | open
```

The runtime places this decision in `context.extension("learning_coordinator")`. Capabilities can read it without adding coordinator fields to `UnifiedContext` or passing mutable state through compatibility metadata.

`finish(decision, capability_output)` accepts only structured completion data. It may append evidence, update the next-activity pointer, or leave the turn unassessed. It cannot assign mastery directly.

## Components

### Scope Detector

The detector classifies each request as `answer`, `lesson`, or `path`.

- `answer`: a narrow factual, explanatory, or problem-specific request. It stays in normal chat or uses a single specialist capability.
- `lesson`: a difficult concept or skill that benefits from a small sequence of explanation and learner action. It starts immediately and stays resumable.
- `path`: a broad field, multi-week goal, or dependency-heavy topic. It produces a proposal and waits for approval before creating durable curriculum.

Deterministic signals handle explicit requests, selected capabilities, existing course bindings, and accepted paths. A structured model classification handles the remaining cases. Low-confidence classification chooses the smallest scope that can answer safely, usually `answer`; the learner can expand it with “teach me this properly” or “make this a path.”

### Teaching Strategist

The strategist maps the objective type to a teaching recipe. Recipes live as versioned declarative data, carry supported activity types and evidence requirements, and remain independently testable.

| Objective type | Default teaching pattern |
| --- | --- |
| Facts and vocabulary | Retrieval, spacing, and interleaving |
| Concepts | Visual model, analogy, prediction, teach-back, then transfer |
| Procedures and problem solving | Worked example, guided attempt, faded help, unfamiliar variation |
| Analysis, interpretation, and design | Competing views, evidence critique, artifact or project, revision |

The strategist follows these rules:

- Diagnose briefly before committing to a teaching route.
- Explain only enough to support the next learner action.
- Treat mistakes as evidence about the blockage, not as a scorekeeping event.
- Require transfer before declaring mastery.
- Ground claims in attached trusted material when the learner supplies it; label outside material plainly.

### Activity Planner

The planner chooses one main activity for the current turn. A plan contains an objective, instructions, expected learner action, help policy, source references, assessment method, and an intended next action.

Supported activity kinds include explanation, prediction, worked example, guided attempt, retrieval, teach-back, evidence comparison, project step, and review. A capability may render several small UI elements, but they must serve one main learner action.

The planner also chooses among four help levels:

0. Independent attempt
1. Framing hint
2. Method hint
3. Partial solution or worked step
4. Complete answer

An explicit “tell me” request moves directly to level 4. Otherwise, the planner increases help after a wrong attempt, a repeated question, a blank response, or an explicit stuck statement. Evidence records the highest help level used.

### Evidence Adapter

The adapter converts a learner interaction into a validated `EvidenceRecord`. It extends the existing `deeptutor.learning` storage and event trail rather than creating a separate progress database.

Each record includes:

```text
evidence_id, learner/thread/path/objective identifiers
activity kind and recipe version
learner response or artifact reference
outcome: correct | partial | incorrect | unassessed
help level, independence, and transfer flags
rubric result, cited evidence, and uncertainty
source references, session ID, turn ID, and timestamp
```

Closed-answer graders may produce evidence directly. Open-ended assessment must return a structured rubric result, the specific parts of the learner's work that support it, and uncertainty. Schema failure, missing support, or excessive uncertainty produces `unassessed`; it never produces an incorrect mark.

Learners can inspect and remove evidence. Removal appends an audit event and triggers a deterministic mastery recalculation.

### Learning Queue

The queue is a projection over existing learning state. It doesn't own another curriculum.

It combines due spaced reviews, the next activity for active learning threads, accepted mastery paths, and course-linked work. Every item contains a short reason such as “due for review,” “needs independent transfer,” or “resume the attempt you left unfinished.”

The coordinator normally selects one queue item per session. It can weave a due review into the current lesson when the context fits; otherwise, it leaves the review queued.

## Durable state

The current mastery store remains authoritative for objectives, attempts, gates, errors, review schedules, interactions, learner overrides, and path leases. The design adds two coordinator concepts to that store:

- `LearningThread`: a resumable goal with `answer`, `lesson`, or `path` scope, status, source bindings, optional course/path binding, and next-activity pointer.
- `EvidenceRecord`: the append-only assessment record described above.

A narrow answer doesn't create a thread. A short lesson creates a session-bound thread after the learner submits the first activity; it appears under “Continue learning” until completion, then archives automatically. Only an explicit save promotes it to a permanent path. A broad request creates a draft thread; approval promotes it into the existing mastery-path machinery.

Preferences remain separate from evidence. Course teaching instructions, interface settings, and learner requests may shape strategy, but they never raise mastery. Existing `LearnerMasteryOverride` records continue to represent explicit learner claims without impersonating assessed evidence.

## Mastery rules

The LLM describes and assesses open work; deterministic policy decides whether the accumulated evidence clears a gate.

Evidence strength follows this order:

1. Independent transfer or a defended project artifact
2. Independent solution or teach-back
3. Correct response after a framing or method hint
4. Correct response after partial or complete guidance
5. Learner self-report

Self-report never counts as assessed mastery. A response shown at help level 4 may prove engagement or diagnose confusion, but it can't count as independent performance.

The first implementation will preserve the current per-type mastery model while extending its inputs:

- Memory objectives require repeated independent retrieval, including delayed evidence.
- Concept objectives require a valid teach-back or an independent transfer response.
- Procedure objectives require an independent solution and a variation that changes the surface form.
- Design objectives require a rubric-scored artifact plus critique, defense, or revision evidence.

Existing quiz attempts remain valid evidence through an adapter. No migration may silently raise or lower a learner's stored mastery.

## Learner-facing flow

### Start

The primary action asks, “What do you want to understand?” The learner can ask a question, attach material, or name a field. They don't select a capability.

The same surface shows one clear “Continue learning” item and a compact due-review count. DeepTutor explains the reason for the next activity on demand.

### Broad path proposal

For `path` scope, DeepTutor presents an editable goal, assumed starting point, selected sources, teaching preferences, ordered outline, and rough session count. The learner can reorder or remove units and change source boundaries.

No path gets created until the learner approves it. Later structural changes remain editable and retain prior evidence only when objective identity still matches.

### Active lesson

The lesson centers one activity. Secondary controls expose:

- “Give me a hint” and “Explain directly”
- “Why this next?”
- source details
- current learning evidence
- teaching adjustments such as more visual, more rigorous, or faster

The default view stays quiet. Rubrics, citations, evidence history, and routing rationale open only when requested.

## Uncertainty and recovery

Learner mistakes trigger diagnosis and the smallest useful intervention; a wrong answer doesn't automatically cause a full re-lecture.

If the tutor lacks enough support, it says what remains uncertain and keeps trusted supplied material separate from outside knowledge. It never invents a source.

Assessment failures produce `unassessed` evidence. They don't lower mastery. Tool or capability failures preserve the learner's input, emit the existing typed error event, and fall back to a simpler activity only when that fallback can still answer honestly.

Interrupted sessions save their thread, active interaction, help level, and next action. Resumption offers one clear continuation rather than replaying the entire lesson.

Learners can challenge a tutoring claim, change the teaching method, request the full answer, inspect evidence, or delete an evidence record. Recalculation uses the remaining records.

## Capability routing

The coordinator selects existing capabilities according to the activity, context, and registered availability:

| Need | Preferred route |
| --- | --- |
| Narrow explanation or guided exchange | `chat` |
| Bound objective with mastery evidence | `mastery_path` |
| Multi-stage mathematical or technical solution | `deep_solve` |
| Diagram, simulation, or animated explanation | `visualize` or `math_animator` |
| Source-centered close reading | `reading` |
| Course-bound planning and resource work | `course_study` |

The coordinator must check the registry rather than assume a capability exists. Missing optional capabilities degrade to `chat` with the plan and source policy intact. It must never claim that a visualization, calculation, or source lookup ran when it didn't.

## Rollout

### Phase 1: decisions and contracts

Add typed coordinator models, scope detection, strategy recipes, the activity planner, and an opt-in preparation hook around current routing. Log decisions without changing learner-visible behavior until scenario tests pass.

### Phase 2: evidence and resumption

Add learning threads, evidence records, validation, mastery input adaptation, queue projection, and safe replay after interruption. Existing mastery data must produce the same gate results before new evidence enters.

### Phase 3: learner experience

Enable automatic routing for opted-in users, add the start/path/lesson surfaces, and connect hint, direct-answer, rationale, source, and evidence controls. Broad path approval remains a hard server-side gate.

### Phase 4: teaching evaluation

Run the coordinator and ordinary chat against the same scenario set, then inspect factual correctness, help dependence, transfer, delayed recall, time, and cost. Keep the coordinator behind a setting until it clears the release gates below.

## Testing

### Contract and unit tests

- Scope selection, explicit overrides, low-confidence fallback, and registry fallback.
- Recipe validation, one-activity planning, help escalation, and source policy.
- Evidence schema validation, `unassessed` behavior, removal, replay, and deterministic recalculation.
- Queue ordering, interrupted-turn recovery, idempotency, and path lease behavior.

### Integration tests

Run the same prepared turn through CLI, WebSocket, and Python SDK adapters. Verify stream ordering, completion metadata, course/path binding, path approval, capability failure fallback, and resume behavior.

### Teaching scenario set

Maintain cases across mathematics, physical science, programming, humanities, and open-ended analysis. Each domain includes narrow questions, difficult concepts, broad goals, common misconceptions, stuck signals, explicit answer requests, weak source support, and interrupted sessions.

Human reviewers score factual correctness, method fit, diagnosis quality, source honesty, cognitive load, and whether the final check requires independent transfer. Model grading may help sort results, but it can't certify its own teaching.

### Release gates

- All existing tests pass, including current mastery and capability-routing suites.
- Every broad-path case requires approval before durable path creation.
- Every explicit direct-answer case supplies the answer without forcing another attempt.
- Every invalid open-ended assessment becomes `unassessed`; no malformed record changes mastery.
- The coordinator beats ordinary chat on the blinded transfer rubric and doesn't regress factual correctness, source honesty, or successful task completion.

The first release is an opt-in beta. Production default changes only after real learner sessions confirm that stronger transfer doesn't come with unacceptable frustration, latency, or cost.

## Likely code boundaries

Implementation should keep coordinator units small:

```text
deeptutor/learning/coordinator/
    models.py          typed decisions, activities, threads, evidence
    scope.py           deterministic and model-assisted classification
    strategies.py      recipe loading and selection
    planner.py         one-activity planning and help policy
    evidence.py        validation and mastery adaptation
    queue.py           next-action projection
    service.py         prepare/finish public interface
```

The runtime change belongs behind a narrow injected hook in `deeptutor/runtime/orchestrator.py`. Storage changes stay in `deeptutor.learning`; API and web changes consume the same typed contracts. Prompt files follow DeepTutor's existing English/Chinese layout.

No implementation file should need to understand courses, reading, mastery internals, and UI rendering at once. Adapters own those boundaries.

## Acceptance criteria

The work satisfies this design when a learner can enter one request and DeepTutor can:

1. choose answer, lesson, or proposed path without exposing mode selection;
2. carry out one appropriate activity through an existing capability;
3. preserve source boundaries and honor direct-answer requests;
4. record valid, inspectable evidence without letting the LLM write mastery;
5. resume with one justified next action and prove better transfer than normal chat in the agreed evaluation.

## References

- [OpenMAIC repository at the reviewed commit](https://github.com/THU-MAIC/OpenMAIC/tree/f760f58a70e6d624a8e49dcb2f7bfbda8c1069e1)
- `deeptutor/runtime/orchestrator.py`
- `deeptutor/core/context.py`
- `deeptutor/capabilities/mastery/capability.py`
- `deeptutor/learning/`
- `deeptutor/capabilities/course_study/`
- `deeptutor/services/courses_state.py`
