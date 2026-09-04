import { expect, test, type Page } from "@playwright/test";

const PATH_THREAD_ID = "learning-thread-path";
const LESSON_THREAD_ID = "learning-thread-lesson";
const LESSON_SESSION_ID = "learning-session-active";

function activity(scope: "path" | "lesson") {
  return {
    kind: scope === "path" ? "explanation" : "guided_attempt",
    objective:
      scope === "path"
        ? "Plan a Fourier learning route"
        : "Identify the dominant frequency",
    learner_action:
      scope === "path"
        ? "Review and approve the route."
        : "Predict which frequency dominates the signal.",
    knowledge_type: "concept",
    recipe_id: "concept-transfer",
    recipe_version: 1,
    recipe_step: 0,
    help_level: 0,
    source_refs: scope === "lesson" ? ["source://fourier-intro"] : [],
    assessment_method: "rubric",
    independent_required: scope === "lesson",
    transfer_required: false,
    next_action: scope === "path" ? "Approve the path" : "Answer the prompt",
  };
}

function learningDecision(scope: "path" | "lesson") {
  return {
    scope,
    route: "chat",
    goal: "Understand Fourier transforms",
    language: "en",
    thread_id: scope === "path" ? PATH_THREAD_ID : LESSON_THREAD_ID,
    objective_id: "fourier-frequency",
    activity: activity(scope),
    reason:
      scope === "path"
        ? "The broad goal needs an ordered path."
        : "Prediction makes the learner's current model visible.",
    confidence: 0.92,
    requires_approval: scope === "path",
    source_policy: "open",
  };
}

async function installTurnTransport(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem("deeptutor-language", "en");
    window.localStorage.setItem("deeptutor-response-language", "en");
    type CapturedCommand = Record<string, unknown>;
    const host = window as typeof window & {
      __learningTurnCommands?: CapturedCommand[];
    };
    const NativeWebSocket = window.WebSocket;

    class LearningWebSocket extends EventTarget {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSING = 2;
      static readonly CLOSED = 3;

      readonly url: string;
      readyState = LearningWebSocket.CONNECTING;

      constructor(url: string | URL) {
        super();
        this.url = String(url);
        if (this.url !== "/ws" && !this.url.endsWith("/ws")) {
          return new NativeWebSocket(url) as unknown as LearningWebSocket;
        }
        queueMicrotask(() => {
          this.readyState = LearningWebSocket.OPEN;
          this.dispatchEvent(new Event("open"));
        });
      }

      send(data: string) {
        const command = JSON.parse(data) as CapturedCommand;
        (host.__learningTurnCommands ||= []).push(command);
        if (
          command.type !== "start_turn" ||
          command.content !== "Teach me Fourier transforms"
        ) {
          return;
        }

        const emit = (
          seq: number,
          type: string,
          content: string,
          metadata: Record<string, unknown>,
        ) => {
          this.dispatchEvent(
            new MessageEvent("message", {
              data: JSON.stringify({
                protocol_version: "2.0",
                type,
                turn_id: "turn-fourier-path",
                session_id: "session-fourier-path",
                seq,
                timestamp: Date.now() / 1000,
                source: "learning_coordinator",
                stage: "responding",
                content,
                metadata,
              }),
            }),
          );
        };

        setTimeout(() => {
          emit(1, "session", "", {
            session_id: "session-fourier-path",
            turn_id: "turn-fourier-path",
          });
          emit(2, "tool_result", "", { proposal: pathDraftForBrowser });
          emit(3, "result", "", {
            learning_decision: pathDecisionForBrowser,
          });
          emit(4, "content", "I drafted an editable learning path.", {});
          emit(5, "done", "", {
            status: "completed",
            user_message_id: 1,
            assistant_message_id: 2,
            learning_decision: pathDecisionForBrowser,
          });
        }, 0);
      }

      close() {
        if (this.readyState === LearningWebSocket.CLOSED) return;
        this.readyState = LearningWebSocket.CLOSED;
        this.dispatchEvent(new CloseEvent("close"));
      }
    }

    const pathDraftForBrowser = {
      path_id: "fourier-draft",
      name: "Fourier transforms",
      goal: "Understand Fourier transforms",
      description: "A focused route from signals to spectra.",
      starting_point: "Comfortable with algebra and trigonometry",
      teaching_preferences: "Use concrete signal examples",
      sources: [
        {
          id: "source-1",
          kind: "note",
          source_id: "fourier-intro",
          label: "Fourier introduction",
          excerpt: "A signal can be decomposed into frequencies.",
          position: 0,
          available: true,
          metadata: {},
        },
      ],
      modules: [
        {
          id: "module-1",
          name: "Signals and frequency",
          order: 0,
          pass_threshold: 0.7,
          knowledge_points: [
            {
              id: "fourier-frequency",
              name: "Relate a signal to its frequencies",
              type: "concept",
              module_id: "module-1",
            },
          ],
        },
      ],
    };
    const pathDecisionForBrowser = {
      scope: "path",
      route: "chat",
      goal: "Understand Fourier transforms",
      language: "en",
      thread_id: "learning-thread-path",
      objective_id: "fourier-frequency",
      activity: {
        kind: "explanation",
        objective: "Plan a Fourier learning route",
        learner_action: "Review and approve the route.",
        knowledge_type: "concept",
        recipe_id: "concept-transfer",
        recipe_version: 1,
        recipe_step: 0,
        help_level: 0,
        source_refs: [],
        assessment_method: "rubric",
        independent_required: false,
        transfer_required: false,
        next_action: "Approve the path",
      },
      reason: "The broad goal needs an ordered path.",
      confidence: 0.92,
      requires_approval: true,
      source_policy: "open",
    };

    Object.defineProperty(window, "WebSocket", {
      configurable: true,
      writable: true,
      value: LearningWebSocket,
    });
  });
}

async function runLearningCoordinatorAudit(page: Page) {
  let approvedGoal = "";
  const helpLevels: number[] = [];
  const pageErrors: string[] = [];

  page.on("pageerror", (error) => pageErrors.push(error.message));

  await installTurnTransport(page);
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (payload: unknown, status = 200) =>
      route.fulfill({ status, json: payload });

    if (path === "/api/auth/status") {
      return json({
        enabled: false,
        authenticated: true,
        role: "admin",
        is_admin: true,
      });
    }
    if (path === "/api/settings/ui") {
      return json({ language: "en" });
    }
    if (path === "/api/capabilities/registered") {
      return json({
        capabilities: [{ id: "chat", kind: "turn", available: true }],
      });
    }
    if (path === "/api/settings") {
      return json({
        catalog: {},
        ui: { learning_coordinator_enabled: true },
      });
    }
    if (path === "/api/settings/llm-options") {
      return json({
        active: { profile_id: "profile", model_id: "model" },
        options: [
          {
            profile_id: "profile",
            model_id: "model",
            profile_name: "Profile",
            model_name: "Model",
            model: "model",
            provider: "provider",
            is_active_default: true,
          },
        ],
      });
    }
    if (path === "/api/dashboard/suggestions") {
      return json({ suggestions: [], stale: false });
    }
    if (path === "/api/courses") return json({ courses: [] });
    if (path === "/api/knowledge-bases") return json([]);
    if (path === "/api/tools") return json({ enabled_optional_tools: [] });
    if (path === "/api/subagents/settings") {
      return json({ default_consult_budget: 1 });
    }
    if (path === "/api/settings/chat-attachments") {
      return json({
        effective: {
          max_file_bytes: 20_000_000,
          max_total_bytes: 50_000_000,
        },
      });
    }
    if (path === "/api/learning/queue") return json({ items: [] });
    if (
      path === `/api/learning/threads/${PATH_THREAD_ID}/approve-path` &&
      request.method() === "POST"
    ) {
      approvedGoal = (request.postDataJSON() as { goal: string }).goal;
      return json({ path_id: "path-approved" });
    }
    if (path === "/api/mastery-paths/topics/path-approved") {
      return json({
        path_id: "path-approved",
        name: "Fourier transforms",
        metadata: {
          path_id: "path-approved",
          goal: "Analyze unfamiliar signals",
          description: "A focused route from signals to spectra.",
          emoji: "",
          map_seed: 0,
          status: "active",
          created_at: 1,
          updated_at: 2,
        },
        sources: [],
        path_revision: 1,
        next: {
          action: "practice",
          knowledge_point_id: "fourier-frequency",
          knowledge_point_name: "Relate a signal to its frequencies",
          knowledge_point_type: "concept",
          status: "new",
          gate: "qualitative",
          mastery: 0,
          threshold: 0.7,
          reason: "Build the signal model first.",
          pending_prompt: "",
          session_id: "",
        },
        map: {
          name: "Fourier transforms",
          counts: { mastered: 0, learning: 0, new: 1, total: 1 },
          due_reviews: 0,
          complete: false,
          modules: [
            {
              id: "module-1",
              name: "Signals and frequency",
              order: 0,
              mastered: 0,
              total: 1,
              knowledge_points: [
                {
                  id: "fourier-frequency",
                  name: "Relate a signal to its frequencies",
                  type: "concept",
                  status: "new",
                  mastery: 0,
                  mastery_source: "",
                  override_note: "",
                },
              ],
            },
          ],
        },
        reviews: [],
        session_count: 0,
        updated_at: 2,
      });
    }
    if (path === "/api/mastery-paths/topics/path-approved/sessions") {
      return json({ path_id: "path-approved", sessions: [] });
    }
    if (path === "/api/mastery-paths/progress/path-approved/events") {
      return json({ events: [] });
    }
    if (path === `/api/sessions/${LESSON_SESSION_ID}`) {
      return json({
        id: LESSON_SESSION_ID,
        session_id: LESSON_SESSION_ID,
        title: "Fourier lesson",
        created_at: 1,
        updated_at: Date.now() / 1000,
        status: "completed",
        preferences: { capability: "chat", language: "en" },
        active_turns: [],
        messages: [
          {
            id: 10,
            session_id: LESSON_SESSION_ID,
            role: "user",
            content: "Begin the first activity",
            capability: "chat",
            events: [],
            attachments: [],
            created_at: 1,
            parent_message_id: null,
          },
          {
            id: 11,
            session_id: LESSON_SESSION_ID,
            role: "assistant",
            content: "Let's inspect the signal before calculating.",
            capability: "chat",
            events: [
              {
                protocol_version: "2.0",
                type: "done",
                turn_id: "turn-fourier-lesson",
                session_id: LESSON_SESSION_ID,
                seq: 1,
                timestamp: 2,
                source: "learning_coordinator",
                stage: "responding",
                content: "",
                metadata: { learning_decision: learningDecision("lesson") },
              },
            ],
            attachments: [],
            created_at: 2,
            parent_message_id: 10,
          },
        ],
      });
    }
    if (path === `/api/sessions/${LESSON_SESSION_ID}/ask-hint`) {
      return json({ hint: "" });
    }
    if (path === `/api/learning/threads/${LESSON_THREAD_ID}/evidence`) {
      return json({
        evidence: [
          {
            evidence_id: "evidence-1",
            thread_id: LESSON_THREAD_ID,
            session_id: LESSON_SESSION_ID,
            objective_id: "fourier-frequency",
            path_id: "path-approved",
            activity_kind: "guided_attempt",
            recipe_id: "concept-transfer",
            recipe_version: 1,
            outcome: "partial",
            help_level: 0,
            independent: true,
            transfer: false,
            artifact_ref: "",
            response: "The low frequency looks strongest.",
            response_ref: "turn-fourier-lesson",
            source_refs: ["source://fourier-intro"],
          },
        ],
      });
    }
    if (
      path === `/api/learning/threads/${LESSON_THREAD_ID}/help` &&
      request.method() === "POST"
    ) {
      helpLevels.push(
        (request.postDataJSON() as { help_level: number }).help_level,
      );
      return json({
        thread: {
          thread_id: LESSON_THREAD_ID,
          session_id: LESSON_SESSION_ID,
          scope: "lesson",
          status: "active",
          goal: "Understand Fourier transforms",
          course_id: "",
          path_id: "path-approved",
        },
      });
    }
    if (path === "/api/sessions") return json({ sessions: [] });
    return json({});
  });

  await page.goto("/chat");
  await expect(
    page.getByRole("heading", { name: "What do you want to understand?" }),
  ).toBeVisible();
  await page.getByRole("textbox").last().fill("Teach me Fourier transforms");
  await page.getByRole("button", { name: "Send", exact: true }).click();

  await expect(
    page.getByRole("heading", { name: "Proposed learning path" }),
  ).toBeVisible();
  await expect(page).toHaveURL(/\/chat\/session-fourier-path$/);
  await page
    .getByLabel("I want to be able to")
    .fill("Analyze unfamiliar signals");
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth ===
        document.documentElement.clientWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "Approve path and begin" }).click();
  await expect(page).toHaveURL(/\/mastery\/path-approved$/);
  expect(approvedGoal).toBe("Analyze unfamiliar signals");
  await expect(
    page.getByRole("heading", { name: "Fourier transforms" }),
  ).toBeVisible();
  await expect.poll(() => pageErrors).toEqual([]);
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth ===
        document.documentElement.clientWidth,
    ),
  ).toBe(true);

  await page.goto(`/chat/${LESSON_SESSION_ID}`);
  await page.getByRole("button", { name: "Activity", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Identify the dominant frequency" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Hint", exact: true }).click();
  await expect.poll(() => helpLevels).toEqual([1]);
  await expect
    .poll(() =>
      page.evaluate(() => {
        const commands = (
          window as typeof window & {
            __learningTurnCommands?: Array<Record<string, unknown>>;
          }
        ).__learningTurnCommands;
        return commands?.some(
          (command) =>
            command.type === "start_turn" &&
            command.content === "Give me the next hint." &&
            command.learning_coordinator === true &&
            command.learning_thread_id === "learning-thread-lesson",
        );
      }),
    )
    .toBe(true);

  await page.getByText("Learning evidence", { exact: true }).click();
  await expect(page.getByText("partial: guided_attempt")).toBeVisible();
  await page.getByText("Sources", { exact: true }).click();
  await expect(page.getByText("source://fourier-intro")).toBeVisible();
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth ===
        document.documentElement.clientWidth,
    ),
  ).toBe(true);
  expect(pageErrors).toEqual([]);
}

test("broad learning flow at the configured desktop viewport", async ({ page }) => {
  expect(page.viewportSize()?.width).toBeGreaterThan(320);
  await runLearningCoordinatorAudit(page);
});

test("broad learning flow remains usable at 320 pixels", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 760 });
  await runLearningCoordinatorAudit(page);
});
