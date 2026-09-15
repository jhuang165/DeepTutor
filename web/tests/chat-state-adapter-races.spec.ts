import { describe, expect, it } from "vitest";

import { chatStateAdapterTestUtils } from "@/features/chat/ChatStateAdapter";
import type { StreamEvent } from "@/features/chat/model/protocol";

const { createSessionEntry, initialState, reducer } = chatStateAdapterTestUtils;

function contentEvent(content: string): StreamEvent {
  return {
    type: "content",
    source: "assistant",
    stage: "answer",
    content,
    metadata: {},
    timestamp: 1,
    turn_id: "turn-1",
    seq: 1,
  };
}

describe("ChatStateAdapter race safeguards", () => {
  it("routes late draft actions to the bound canonical session without duplicates", () => {
    const draft = {
      ...createSessionEntry("draft-1"),
      messages: [{ id: -1, role: "user" as const, content: "question" }],
      isStreaming: true,
      status: "running" as const,
    };
    let state: typeof initialState = {
      ...initialState,
      selectedKey: draft.key,
      sessions: { [draft.key]: draft },
    };

    state = reducer(state, {
      type: "BIND_SERVER_SESSION",
      key: draft.key,
      sessionId: "session-1",
      turnId: "turn-1",
    });
    state = reducer(state, {
      type: "STREAM_EVENT",
      key: draft.key,
      event: contentEvent("answer"),
    });
    state = reducer(state, { type: "STREAM_TOUCH", key: draft.key });
    state = reducer(state, {
      type: "RECONCILE_TURN",
      key: draft.key,
      turnId: "turn-1",
      userMessageId: 10,
      assistantMessageId: 11,
    });
    state = reducer(state, {
      type: "SET_SESSION_TITLE",
      key: draft.key,
      title: "Canonical title",
    });
    state = reducer(state, {
      type: "SET_MASTERY_PATH_ID",
      key: draft.key,
      masteryPathId: "path-1",
    });
    state = reducer(state, {
      type: "STREAM_END",
      key: draft.key,
      status: "completed",
    });

    expect(Object.keys(state.sessions)).toEqual(["session-1"]);
    expect(state.selectedKey).toBe("session-1");
    expect(state.sessions["session-1"]).toMatchObject({
      sessionId: "session-1",
      sessionTitle: "Canonical title",
      masteryPathId: "path-1",
      isStreaming: false,
      status: "completed",
    });
    expect(state.sessions["session-1"].messages).toMatchObject([
      { id: 10, content: "question" },
      { id: 11, content: "answer" },
    ]);

    const rebound = reducer(state, {
      type: "BIND_SERVER_SESSION",
      key: draft.key,
      sessionId: "session-1",
      turnId: "turn-1",
    });
    expect(rebound).toBe(state);
  });

  it("records a new turn id when an existing session binds again", () => {
    const session = {
      ...createSessionEntry("session-1", "session-1"),
      isStreaming: true,
      status: "running" as const,
    };
    const state: typeof initialState = {
      ...initialState,
      selectedKey: session.key,
      sessions: { [session.key]: session },
    };

    const result = reducer(state, {
      type: "BIND_SERVER_SESSION",
      key: session.key,
      sessionId: session.key,
      turnId: "turn-2",
    });

    expect(result.sessions[session.key].activeTurnId).toBe("turn-2");
  });

  it("bounds retained retired-key aliases", () => {
    let state = initialState;
    for (let index = 0; index < 25; index += 1) {
      const key = `draft-${index}`;
      state = {
        ...state,
        sessions: { ...state.sessions, [key]: createSessionEntry(key) },
      };
      state = reducer(state, {
        type: "BIND_SERVER_SESSION",
        key,
        sessionId: `session-${index}`,
      });
    }

    expect(Object.keys(state.sessionAliases)).toHaveLength(20);
    expect(state.sessionAliases["draft-0"]).toBeUndefined();
    expect(state.sessionAliases["draft-24"]).toBe("session-24");
  });

  it("rejects a revalidation snapshot requested before a local mutation", () => {
    const session = {
      ...createSessionEntry("session-1", "session-1"),
      messages: [{ id: 1, role: "user" as const, content: "persisted" }],
    };
    let state: typeof initialState = {
      ...initialState,
      selectedKey: session.key,
      sessions: { [session.key]: session },
    };
    const requestedRevision = session.revision;

    state = reducer(state, {
      type: "SET_SELECTED_BRANCH",
      key: session.key,
      parentKey: "null",
      childId: 1,
    });
    const result = reducer(state, {
      type: "REVALIDATE_SESSION",
      key: session.key,
      sessionId: session.key,
      messages: [],
      requestedRevision,
    });

    expect(result).toBe(state);
    expect(result.sessions[session.key].messages).toHaveLength(1);
  });

  it("applies an authoritative refresh when the revision is still current", () => {
    const session = {
      ...createSessionEntry("session-1", "session-1"),
      messages: [{ id: -1, role: "assistant" as const, content: "partial" }],
      isStreaming: true,
      status: "running" as const,
    };
    const state = {
      ...initialState,
      selectedKey: session.key,
      sessions: { [session.key]: session },
    };

    const result = reducer(state, {
      type: "REVALIDATE_SESSION",
      key: session.key,
      sessionId: session.key,
      messages: [{ id: 2, role: "assistant", content: "complete" }],
      status: "completed",
      requestedRevision: session.revision,
      authoritative: true,
    });

    expect(result.sessions[session.key]).toMatchObject({
      isStreaming: false,
      status: "completed",
    });
    expect(result.sessions[session.key].messages[0].content).toBe("complete");
  });
});
