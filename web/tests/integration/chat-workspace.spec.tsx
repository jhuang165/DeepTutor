import fs from "node:fs";
import path from "node:path";
import React from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import shadowDecisions from "../fixtures/learning-shadow-decisions.json";
import { parseLearningDecision } from "@/features/learning/model";

import ChatWorkspace from "@/features/chat/components/ChatWorkspace";

const workspace = vi.hoisted(() => {
  const fn = () => vi.fn();
  return {
    preference: true,
    settingsResponse: null as Promise<Response> | null,
    queueFails: false,
    helpFails: false,
    evidenceFails: false,
    queueItems: [] as any[],
    apiCalls: [] as Array<{ url: string; init?: RequestInit }>,
    state: {
      sessionId: null as string | null,
      sessionTitle: "New conversation",
      enabledTools: [] as string[],
      activeCapability: null as string | null,
      workspaceMode: null,
      knowledgeBases: [] as string[],
      llmSelection: null,
      masteryPathId: null as string | null,
      courseId: "",
      personaSelection: "",
      messages: [] as any[],
      isStreaming: false,
      currentStage: "",
      language: "en",
      selectedBranches: {} as Record<string, number>,
    },
    router: { replace: fn(), push: fn() },
    adapter: {
      setTools: fn(),
      setCapability: fn(),
      setKBs: fn(),
      setLLMSelection: fn(),
      setPersonaSelection: fn(),
      sendMessage: fn(),
      cancelStreamingTurn: fn(),
      submitUserReply: fn(),
      regenerateLastMessage: fn(),
      deleteTurn: fn(),
      editMessage: fn(),
      switchBranch: fn(),
      newSession: fn(),
      loadSession: fn(),
      showCachedSession: fn(),
      renameSessionTitle: fn(),
      setCourseId: fn(),
    },
  };
});

vi.mock("next/dynamic", () => ({ default: () => () => null }));
vi.mock("@/features/chat/controllers/useChatRouteSession", () => ({
  useChatRouteSession: () => ({ router: workspace.router, sessionId: null }),
}));
vi.mock("@/features/chat/ChatStateAdapter", () => ({
  useChatStateAdapter: () => ({ state: workspace.state, ...workspace.adapter }),
}));
vi.mock("@/context/AppShellContext", () => ({
  useAppShell: () => ({ setActiveSessionId: vi.fn(), language: "en" }),
}));
vi.mock("@/features/capabilities/useCapabilityCatalog", () => ({
  useCapabilityCatalog: () => ({
    capabilities: [],
    visibleCapabilities: [],
    isLoading: false,
  }),
}));
vi.mock("@/hooks/useLLMOptions", () => ({
  useLLMOptions: () => ({
    options: [],
    activeDefault: null,
    loading: false,
    error: false,
    refresh: vi.fn(),
  }),
}));
vi.mock("@/hooks/useChatAutoScroll", () => ({
  useChatAutoScroll: () => ({
    containerRef: { current: null },
    endRef: { current: null },
    shouldAutoScrollRef: { current: true },
    scrollToBottom: vi.fn(),
    handleScroll: vi.fn(),
  }),
}));
vi.mock("@/hooks/useMeasuredHeight", () => ({
  useMeasuredHeight: () => ({ ref: { current: null }, height: 0 }),
}));
vi.mock("@/hooks/useSetupSync", () => ({ useSetupSync: vi.fn() }));
vi.mock("@/hooks/useVoiceRecorder", () => ({
  useVoiceRecorder: () => ({
    state: "idle",
    error: null,
    toggle: vi.fn(),
    start: vi.fn(),
    stop: vi.fn(),
  }),
}));
vi.mock("@/lib/attachment-limits", () => ({
  useAttachmentLimits: () => ({
    maxFiles: 5,
    maxFileBytes: 1_000_000,
    maxTotalBytes: 5_000_000,
  }),
}));
vi.mock("@/lib/courses-api", () => ({ listCourses: vi.fn(async () => []) }));
vi.mock("@/features/knowledge/api/catalog", () => ({
  listKnowledgeBases: vi.fn(async () => []),
}));
vi.mock("@/lib/subagents-api", () => ({
  getSubagentSettings: vi.fn(async () => ({ consult_budget: 1 })),
}));
vi.mock("@/lib/tools-settings", () => ({
  getEnabledOptionalTools: vi.fn(async () => []),
  invalidateEnabledOptionalToolsCache: vi.fn(),
}));
vi.mock("@/lib/api", () => ({
  apiUrl: (value: string) => value,
  apiFetch: vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    workspace.apiCalls.push({ url, init });
    if (url === "/api/settings" && workspace.settingsResponse) {
      return workspace.settingsResponse;
    }
    if (url.includes("/api/learning/queue")) {
      if (workspace.queueFails) throw new Error("offline");
      return {
        ok: true,
        json: async () => ({ items: workspace.queueItems }),
      } as Response;
    }
    if (url === "/api/learning/threads/thread-1") {
      return {
        ok: true,
        json: async () => ({ thread: { session_id: "session-previous" } }),
      } as Response;
    }
    if (url.includes("/evidence")) {
      if (workspace.evidenceFails) {
        return { ok: false, status: 503, json: async () => ({}) } as Response;
      }
      return { ok: true, json: async () => ({ evidence: [] }) } as Response;
    }
    if (url.includes("/help")) {
      if (workspace.helpFails) {
        return { ok: false, status: 503, json: async () => ({}) } as Response;
      }
      return { ok: true, json: async () => ({ thread: {} }) } as Response;
    }
    if (url.includes("/approve-path")) {
      return {
        ok: true,
        json: async () => ({ path_id: "path-1" }),
      } as Response;
    }
    return {
      ok: true,
      json: async () => ({
        ui: { learning_coordinator_enabled: workspace.preference },
      }),
    } as Response;
  }),
}));
vi.mock("@/components/chat/preview/FilePreviewDrawer", () => ({
  default: () => null,
}));
vi.mock("@/components/chat/home/StarterSuggestions", () => ({
  default: () => null,
}));
vi.mock("@/features/chat/messages", () => ({
  ChatMessageList: () => <div data-testid="message-list" />,
}));
vi.mock("@/components/chat/home/SessionViewerPanel", () => {
  const MockSessionViewerPanel = React.forwardRef(
    (
      {
        open,
        configSection,
      }: { open: boolean; configSection?: React.ReactNode },
      _ref,
    ) =>
      open ? <aside aria-label="Activity panel">{configSection}</aside> : null,
  );
  MockSessionViewerPanel.displayName = "MockSessionViewerPanel";
  return { default: MockSessionViewerPanel };
});
vi.mock("@/context/QuizFollowupContext", () => ({
  QuizFollowupProvider: ({ children }: { children: React.ReactNode }) =>
    children,
  useQuizFollowupController: () => ({ setOpenTabHandler: vi.fn() }),
}));
vi.mock("@/context/GeogebraTabContext", () => ({
  GeogebraTabProvider: ({ children }: { children: React.ReactNode }) =>
    children,
  useGeogebraTabOpener: () => ({ setOpenHandler: vi.fn() }),
}));

const source = (relative: string) =>
  fs.readFileSync(path.resolve(process.cwd(), relative), "utf8");

function setActiveLesson() {
  workspace.state.sessionId = "session-1";
  workspace.state.messages = [
    {
      id: 1,
      role: "assistant",
      content: "Try the activity.",
      events: [
        {
          type: "done",
          metadata: {
            learning_decision: {
              scope: "lesson",
              route: "chat",
              goal: "Understand signals",
              language: "en",
              thread_id: "thread-1",
              objective_id: "objective-1",
              activity: {
                kind: "guided_attempt",
                objective: "Relate frequency to a signal",
                learner_action: "Predict the dominant frequency.",
                knowledge_type: "concept",
                recipe_id: "concept-transfer",
                recipe_version: 1,
                recipe_step: 0,
                help_level: 0,
                source_refs: ["source-1"],
                assessment_method: "rubric",
                independent_required: true,
                transfer_required: false,
                next_action: "Answer the prompt",
              },
              reason: "Prediction exposes the current model.",
              confidence: 0.8,
              requires_approval: false,
              source_policy: "open",
            },
          },
        },
      ],
    },
  ];
}

function setPathProposal() {
  workspace.state.sessionId = "session-1";
  workspace.state.messages = [
    {
      id: 1,
      role: "assistant",
      content: "I drafted a path.",
      events: [
        {
          type: "tool_result",
          metadata: {
            proposal: {
              path_id: "draft-1",
              name: "Signals",
              goal: "Understand signals",
              description: "An editable route.",
              starting_point: "Basic algebra",
              teaching_preferences: "Use examples",
              sources: [],
              modules: [
                {
                  id: "module-1",
                  name: "Foundations",
                  order: 0,
                  pass_threshold: 0.7,
                  knowledge_points: [
                    {
                      id: "objective-1",
                      name: "Frequency",
                      type: "concept",
                      module_id: "module-1",
                    },
                  ],
                },
              ],
            },
          },
        },
        {
          type: "done",
          metadata: {
            learning_decision: {
              scope: "path",
              route: "chat",
              goal: "Understand signals",
              language: "en",
              thread_id: "thread-1",
              objective_id: "objective-1",
              activity: {
                kind: "explanation",
                objective: "Plan a route",
                learner_action: "Review the proposed route.",
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
              reason: "This broad goal needs an ordered path.",
              confidence: 0.9,
              requires_approval: true,
              source_policy: "open",
            },
          },
        },
      ],
    },
  ];
}

describe("chat workspace composition", () => {
  it("keeps the route as a small composition boundary", () => {
    const route = [
      source("app/(workspace)/chat/page.tsx"),
      source("app/(workspace)/chat/[sessionId]/page.tsx"),
    ].join("\n");
    expect(route).toMatch(/<ChatWorkspace/);
    expect(route).not.toMatch(
      /apiFetch|localStorage|useState|useEffect|modal/i,
    );
    expect(route.split("\n").length).toBeLessThan(20);
  });

  it("moves session resolution behind its route controller", () => {
    const workspace = source("features/chat/components/ChatWorkspace.tsx");
    expect(workspace).toMatch(/useChatRouteSession/);
    expect(workspace).not.toMatch(/useParams|useRouter/);
  });
});

describe("learning coordinator workspace", () => {
  it.each(['lesson', 'path'])(
    'keeps shadow %s metadata observational through rerender and the next send',
    async scope => {
      // Actual runtime shadow decisions have no persisted thread identity, even
      // when their hypothetical route/scope would have started a lesson/path.
      workspace.preference = false
      if (scope === 'path') setPathProposal()
      else setActiveLesson()
      const shadowEvents = workspace.state.messages[0].events
      const metadata = shadowDecisions[scope as 'lesson' | 'path']
      expect(parseLearningDecision(metadata.learning_decision)?.scope).toBe(scope)
      const proposalEvent = scope === 'path' ? [shadowEvents[0]] : []
      workspace.state.messages = []
      const user = userEvent.setup()
      const { rerender } = render(<ChatWorkspace />)
      await waitFor(() =>
        expect(workspace.apiCalls.some(({ url }) => url === '/api/settings')).toBe(true)
      )
      workspace.state.messages = [
        {
          id: 2,
          role: 'assistant',
          content: 'Ordinary chat answer',
          events: [...proposalEvent, { type: 'done', metadata }],
        },
      ]
      rerender(<ChatWorkspace />)
      await user.click(screen.getByRole('button', { name: 'Activity' }))
      expect(
        screen.queryByRole('heading', { name: metadata.learning_decision.activity.objective })
      ).not.toBeInTheDocument()
      expect(
        screen.queryByRole('heading', { name: /proposed learning path/i })
      ).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Tell me directly' })).not.toBeInTheDocument()
      expect(workspace.apiCalls.some(({ url }) => url.includes('/evidence'))).toBe(false)
      await user.type(screen.getByRole('textbox'), 'Continue this explanation')
      await user.click(screen.getByRole('button', { name: 'Send' }))
      expect(workspace.adapter.sendMessage.mock.calls.at(-1)?.[5]).toEqual(
        expect.objectContaining({ learningCoordinator: false })
      )
      expect(workspace.adapter.sendMessage.mock.calls.at(-1)?.[5]?.learningThreadId).toBeFalsy()
    }
  )

  beforeEach(() => {
    workspace.preference = true;
    workspace.settingsResponse = null;
    workspace.queueFails = false;
    workspace.helpFails = false;
    workspace.evidenceFails = false;
    workspace.queueItems = [];
    workspace.apiCalls = [];
    workspace.state.sessionId = null;
    workspace.state.activeCapability = null;
    workspace.state.workspaceMode = null;
    workspace.state.masteryPathId = null;
    workspace.state.courseId = "";
    workspace.state.messages = [];
    workspace.state.isStreaming = false;
    window.localStorage.removeItem("dt:chat:viewer-panel");
    for (const value of Object.values(workspace.adapter)) value.mockClear();
  });

  afterEach(() => cleanup());

  it("asks what the learner wants to understand on an opted-in empty chat", async () => {
    // Production break caught: chat reads the public pre-session UI projection,
    // which intentionally cannot contain the authenticated coordinator opt-in.
    render(<ChatWorkspace />);

    expect(
      await screen.findByRole("heading", {
        name: "What do you want to understand?",
      }),
    ).toBeVisible();
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
    expect(workspace.apiCalls.some(({ url }) => url === "/api/settings")).toBe(true);
  });

  it("preserves the ordinary home when the learner has not opted in", async () => {
    // Break caught: the learner-facing coordinator surface appears without the saved personal opt-in.
    workspace.preference = false;
    render(<ChatWorkspace />);

    expect(await screen.findByRole("textbox")).toBeEnabled();
    expect(
      screen.queryByRole("heading", {
        name: "What do you want to understand?",
      }),
    ).toBeNull();
  });

  it("keeps the composer usable when queue loading fails", async () => {
    // Break caught: a queue error replaces or disables the sole existing chat transport.
    workspace.queueFails = true;
    render(<ChatWorkspace />);

    expect(await screen.findByRole("textbox")).toBeEnabled();
    expect(
      await screen.findByText("Learning suggestions are unavailable."),
    ).toBeVisible();
  });

  it("sends the saved opt-in on a default chat turn", async () => {
    // Break caught: the learner sees the opted-in home but the existing transport starts an ordinary turn.
    const user = userEvent.setup();
    render(<ChatWorkspace />);
    await screen.findByRole("heading", {
      name: "What do you want to understand?",
    });

    await user.type(screen.getByRole("textbox"), "Teach me Fourier transforms");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(workspace.adapter.sendMessage).toHaveBeenCalledWith(
      "Teach me Fourier transforms",
      expect.any(Array),
      expect.any(Object),
      expect.any(Array),
      expect.any(Array),
      expect.objectContaining({ learningCoordinator: true }),
      expect.any(Array),
      undefined,
      expect.any(Array),
    );
  });

  it("does not send an explicit opt-out before personal settings hydrate", async () => {
    // Break caught: a fast default-chat submit turns the temporary false initial state into a backend opt-out.
    let resolveSettings: (response: Response) => void = () => undefined;
    workspace.settingsResponse = new Promise<Response>((resolve) => {
      resolveSettings = resolve;
    });
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox"), "Teach me Fourier transforms");
    await user.click(screen.getByRole("button", { name: "Send" }));

    const options = workspace.adapter.sendMessage.mock.calls[0]?.[5] as
      | { learningCoordinator?: boolean }
      | undefined;
    expect(options?.learningCoordinator).toBeUndefined();

    resolveSettings({
      ok: true,
      json: async () => ({ ui: { learning_coordinator_enabled: true } }),
    } as Response);
    expect(
      await screen.findByRole("heading", {
        name: "What do you want to understand?",
      }),
    ).toBeVisible();
  });

  it("opens the owning chat session for a queued lesson", async () => {
    // Break caught: Continue learning does nothing or starts the thread in an unrelated draft session.
    workspace.queueItems = [
      {
        thread_id: "thread-1",
        path_id: "",
        objective_id: "objective-1",
        activity: { kind: "guided_attempt", objective: "Resume signals" },
        reason: "resume_lesson",
        reason_data: {
          objective: "",
          goal: "Understand signals",
          path_name: "",
          answer_state: "",
        },
        priority: 10,
        due_at: null,
      },
    ];
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await user.click(
      await screen.findByRole("button", { name: "Continue learning" }),
    );

    await waitFor(() =>
      expect(workspace.router.push).toHaveBeenCalledWith(
        "/chat/session-previous",
      ),
    );
  });

  it("sends an explicit opt-out when the learner selects a capability", async () => {
    // Break caught: a saved coordinator preference overrides an explicit capability selection.
    workspace.state.activeCapability = "deep_solve";
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox"), "Solve this integral");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(workspace.adapter.sendMessage).toHaveBeenCalledWith(
      "Solve this integral",
      expect.any(Array),
      expect.any(Object),
      expect.any(Array),
      expect.any(Array),
      expect.objectContaining({ learningCoordinator: false }),
      expect.any(Array),
      undefined,
      expect.any(Array),
    );
  });

  it("hides historical learning activity after an explicit capability is selected", async () => {
    // Break caught: an old lesson decision overrides the newly selected capability's Activity layout.
    setActiveLesson();
    workspace.state.activeCapability = "deep_solve";
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await user.click(screen.getByRole("button", { name: "Activity" }));

    expect(
      screen.queryByRole("heading", {
        name: "Relate frequency to a signal",
      }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("message-list")).toBeVisible();
  });

  it("keeps learning activity for a persisted default-chat capability", async () => {
    // Break caught: the context gate mistakes the stored "chat" capability for an explicit non-learning mode.
    setActiveLesson();
    workspace.state.activeCapability = "chat";
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await user.click(screen.getByRole("button", { name: "Activity" }));

    expect(
      await screen.findByRole("heading", {
        name: "Relate frequency to a signal",
      }),
    ).toBeVisible();
  });

  it("shows a path proposal for path-scope metadata", async () => {
    // Break caught: a broad learning decision stays buried in the transcript instead of requiring draft approval.
    setPathProposal();
    render(<ChatWorkspace />);

    expect(
      await screen.findByRole("heading", { name: /proposed learning path/i }),
    ).toBeVisible();
    expect(
      screen.getByRole("textbox", { name: /i want to be able to/i }),
    ).toHaveValue("Understand signals");
  });

  it("keeps a hydrated opt-out authoritative for a historical path proposal", async () => {
    // Break caught: a path-scope decision forces coordinator routing after the saved preference hydrates false.
    let resolveSettings: (response: Response) => void = () => undefined;
    workspace.settingsResponse = new Promise<Response>((resolve) => {
      resolveSettings = resolve;
    });
    setPathProposal();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await screen.findByRole("heading", { name: /proposed learning path/i });
    await act(async () => {
      resolveSettings({
        ok: true,
        json: async () => ({ ui: { learning_coordinator_enabled: false } }),
      } as Response);
    });

    const composer = screen.getAllByRole("textbox").at(-1);
    expect(composer).toBeDefined();
    await user.type(composer!, "Ask an ordinary follow-up");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(workspace.adapter.sendMessage).toHaveBeenCalledWith(
      "Ask an ordinary follow-up",
      expect.any(Array),
      expect.any(Object),
      expect.any(Array),
      expect.any(Array),
      expect.objectContaining({ learningCoordinator: false }),
      expect.any(Array),
      undefined,
      expect.any(Array),
    );
  });

  it("renders the active lesson in Activity and sends a hint through the existing transport", async () => {
    // Break caught: lesson metadata has no Activity surface, or hint escalation loses its level/thread on continuation.
    workspace.state.sessionId = "session-1";
    workspace.state.messages = [
      {
        id: 1,
        role: "assistant",
        content: "Try the activity.",
        events: [
          {
            type: "done",
            metadata: {
              learning_decision: {
                scope: "lesson",
                route: "chat",
                goal: "Understand signals",
                language: "en",
                thread_id: "thread-1",
                objective_id: "objective-1",
                activity: {
                  kind: "guided_attempt",
                  objective: "Relate frequency to a signal",
                  learner_action: "Predict the dominant frequency.",
                  knowledge_type: "concept",
                  recipe_id: "concept-transfer",
                  recipe_version: 1,
                  recipe_step: 0,
                  help_level: 0,
                  source_refs: ["source-1"],
                  assessment_method: "rubric",
                  independent_required: true,
                  transfer_required: false,
                  next_action: "Answer the prompt",
                },
                reason: "Prediction exposes the current model.",
                confidence: 0.8,
                requires_approval: false,
                source_policy: "open",
              },
            },
          },
        ],
      },
    ];
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    expect(screen.getByTestId("message-list")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Activity" }));
    expect(
      await screen.findByRole("heading", {
        name: "Relate frequency to a signal",
      }),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Hint" }));
    expect(workspace.apiCalls).toContainEqual(
      expect.objectContaining({
        url: "/api/learning/threads/thread-1/help",
        init: expect.objectContaining({
          body: JSON.stringify({ help_level: 1 }),
        }),
      }),
    );
    expect(workspace.adapter.sendMessage).toHaveBeenCalledWith(
      "Give me the next hint.",
      expect.any(Array),
      expect.anything(),
      expect.any(Array),
      expect.any(Array),
      expect.objectContaining({
        learningCoordinator: true,
        learningThreadId: "thread-1",
      }),
      expect.any(Array),
      undefined,
      expect.any(Array),
    );
  });

  it("reports unavailable evidence without claiming the evidence list is empty", async () => {
    // Break caught: an evidence request failure is flattened into the misleading no-evidence empty state.
    setActiveLesson();
    workspace.evidenceFails = true;
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await user.click(screen.getByRole("button", { name: "Activity" }));
    await user.click(await screen.findByText("Learning evidence"));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Learning evidence is unavailable. Please try again.",
    );
    expect(
      screen.queryByText("No learning evidence yet."),
    ).not.toBeInTheDocument();
  });

  it("requests help level four before sending a direct-answer continuation", async () => {
    // Break caught: Explain directly is mislabeled as a hint or can count as independent evidence.
    setActiveLesson();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await user.click(screen.getByRole("button", { name: "Activity" }));
    await user.click(
      await screen.findByRole("button", { name: "Explain directly" }),
    );

    expect(workspace.apiCalls).toContainEqual(
      expect.objectContaining({
        url: "/api/learning/threads/thread-1/help",
        init: expect.objectContaining({
          body: JSON.stringify({ help_level: 4 }),
        }),
      }),
    );
    await waitFor(() =>
      expect(workspace.adapter.sendMessage).toHaveBeenCalledWith(
        "Please explain the answer directly.",
        expect.any(Array),
        expect.anything(),
        expect.any(Array),
        expect.any(Array),
        expect.objectContaining({
          learningCoordinator: true,
          learningThreadId: "thread-1",
        }),
        expect.any(Array),
        undefined,
        expect.any(Array),
      ),
    );
  });

  it("shows a help error without sending a continuation when persistence fails", async () => {
    // Break caught: failed help persistence leaves a false success notice or an unhandled rejection and still sends chat.
    setActiveLesson();
    workspace.helpFails = true;
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await user.click(screen.getByRole("button", { name: "Activity" }));
    await user.click(
      await screen.findByRole("button", { name: "Explain directly" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Help could not be requested. Please try again.",
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(workspace.adapter.sendMessage).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Explain directly" }),
    ).toBeEnabled();
  });

  it("keeps teaching adjustments scoped to the active learning thread", async () => {
    // Break caught: visual, rigor, or pacing controls mutate global settings or lose the current thread.
    setActiveLesson();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await user.click(screen.getByRole("button", { name: "Activity" }));
    await screen.findByRole("heading", {
      name: "Relate frequency to a signal",
    });
    await user.click(screen.getByRole("button", { name: "More visual" }));
    await user.click(screen.getByRole("button", { name: "More rigorous" }));
    await user.click(screen.getByRole("button", { name: "Slow down" }));

    expect(workspace.adapter.sendMessage).toHaveBeenCalledTimes(3);
    expect(
      workspace.adapter.sendMessage.mock.calls.map((call) => ({
        content: call[0],
        options: call[5],
      })),
    ).toEqual([
      {
        content: "Make the next activity more visual.",
        options: expect.objectContaining({
          learningCoordinator: true,
          learningThreadId: "thread-1",
        }),
      },
      {
        content: "Make the next activity more rigorous.",
        options: expect.objectContaining({
          learningCoordinator: true,
          learningThreadId: "thread-1",
        }),
      },
      {
        content: "Slow down the next activity.",
        options: expect.objectContaining({
          learningCoordinator: true,
          learningThreadId: "thread-1",
        }),
      },
    ]);
  });
});
