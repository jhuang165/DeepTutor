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

  it("restarts the sequence cursor for every turn", () => {
    const session = {
      ...createSessionEntry("session-1", "session-1"),
      activeTurnId: "turn-1",
      lastSeq: 66,
      status: "completed" as const,
    };
    let state: typeof initialState = {
      ...initialState,
      selectedKey: session.key,
      sessions: { [session.key]: session },
    };

    state = reducer(state, {
      type: "STREAM_START",
      key: session.key,
      startedAt: 0,
    });
    expect(state.sessions[session.key].lastSeq).toBe(0);

    state = reducer(state, {
      type: "STREAM_EVENT",
      key: session.key,
      event: { ...contentEvent("next"), turn_id: "turn-2", seq: 1 },
    });
    expect(state.sessions[session.key]).toMatchObject({
      activeTurnId: "turn-2",
      lastSeq: 1,
    });
  });

  it("a new turn's first event restarts the cursor on its own", () => {
    // Regenerate / another tab: no STREAM_START ran here, the turn id on the
    // event is the only signal that the counter has started over.
    const session = {
      ...createSessionEntry("session-1", "session-1"),
      activeTurnId: "turn-1",
      lastSeq: 66,
    };
    const state: typeof initialState = {
      ...initialState,
      selectedKey: session.key,
      sessions: { [session.key]: session },
    };

    const result = reducer(state, {
      type: "STREAM_EVENT",
      key: session.key,
      event: { ...contentEvent("next"), turn_id: "turn-2", seq: 3 },
    });
    expect(result.sessions[session.key].lastSeq).toBe(3);
  });

  it("keeps a paused turn's local transcript over a background snapshot", () => {
    // The ask_user card and the text streamed around it exist only locally;
    // the persisted transcript will not contain the assistant row until the
    // turn finishes. A non-authoritative revalidate must leave it alone.
    const session = {
      ...createSessionEntry("session-1", "session-1"),
      messages: [
        { id: 1, role: "user" as const, content: "quiz me" },
        { id: -1, role: "assistant" as const, content: "", events: [] },
      ],
      isStreaming: false,
      status: "waiting_input" as const,
      activeTurnId: "turn-1",
    };
    const state: typeof initialState = {
      ...initialState,
      selectedKey: session.key,
      sessions: { [session.key]: session },
    };

    const result = reducer(state, {
      type: "REVALIDATE_SESSION",
      key: session.key,
      sessionId: session.key,
      messages: [{ id: 1, role: "user", content: "quiz me" }],
      status: "waiting_input",
      activeTurnId: "turn-1",
      requestedRevision: session.revision,
    });

    expect(result).toBe(state);
  });

  it("a loaded snapshot starts the cursor from zero", () => {
    const session = {
      ...createSessionEntry("session-1", "session-1"),
      lastSeq: 66,
    };
    const state: typeof initialState = {
      ...initialState,
      selectedKey: session.key,
      sessions: { [session.key]: session },
    };

    const result = reducer(state, {
      type: "LOAD_SESSION",
      key: session.key,
      sessionId: session.key,
      messages: [{ id: 1, role: "user", content: "quiz me" }],
      status: "waiting_input",
      activeTurnId: "turn-2",
    });

    expect(result.sessions[session.key]).toMatchObject({
      activeTurnId: "turn-2",
      lastSeq: 0,
      isStreaming: false,
      status: "waiting_input",
    });
  });
});
