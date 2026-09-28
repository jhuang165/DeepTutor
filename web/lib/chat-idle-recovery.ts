import { buildResumeTurn } from "@/contracts/parse/turn-command";

interface IdleTurnRecoveryInput {
  isStreaming: boolean;
  hasPendingUserInput: boolean;
  activeTurnId: string | null;
  lastSeq: number;
  updatedAt: number;
  now: number;
  idleTimeoutMs: number;
}

export type IdleTurnRecoveryDecision =
  | { kind: "none" }
  | { kind: "resubscribe"; message: ReturnType<typeof buildResumeTurn> }
  | { kind: "reconcile" };

/**
 * Decide what the client-side idle watchdog should do.
 *
 * A quiet WebSocket is not proof that a server turn failed. Long research
 * tool calls can legitimately emit nothing for several minutes, and the
 * backend keeps the turn alive when a browser briefly disconnects. When a
 * server turn id is known, re-subscribe from the last received sequence so
 * buffered events (including a missed terminal event) are replayed.
 */
export function decideIdleTurnRecovery(
  input: IdleTurnRecoveryInput,
): IdleTurnRecoveryDecision {
  if (!input.isStreaming || input.hasPendingUserInput) return { kind: "none" };
  if (input.now - input.updatedAt <= input.idleTimeoutMs) {
    return { kind: "none" };
  }
  if (!input.activeTurnId) return { kind: "reconcile" };
  return {
    kind: "resubscribe",
    message: buildResumeTurn({
      turnId: input.activeTurnId,
      afterSeq: input.lastSeq,
    }),
  };
}

/**
 * Whether a session snapshot names a turn this tab must subscribe to.
 *
 * `running` is the obvious case. `waiting_input` is just as live: the turn is
 * parked on an ask_user card and its events so far (including the card) exist
 * only in the turn log, never in the persisted transcript. A page opened or
 * reloaded in that state has to replay them to show the question at all, and
 * has to stay subscribed so the answer's continuation arrives.
 */
export function shouldSubscribeLoadedTurn(
  status: string,
  hasActiveTurn: boolean,
): boolean {
  return hasActiveTurn && (status === "running" || status === "waiting_input");
}

/**
 * Whether the local copy of a session is mid-turn, so a background snapshot
 * must not replace it. A turn paused on an ask_user card counts: the card and
 * the text streamed around it live only in local state.
 */
export function hasLiveLocalTurn(session: {
  isStreaming: boolean;
  status: string;
}): boolean {
  return (
    session.isStreaming ||
    session.status === "running" ||
    session.status === "waiting_input"
  );
}

/**
 * Whether a stored `running` status still describes a live turn.
 *
 * The backend sets a session's status to `running` when a turn starts and
 * clears it when the turn reaches a terminal state. A process that dies
 * mid-turn — a crash, a restart, a killed dev server — never gets to clear
 * it, so the row keeps saying `running` forever. Opening such a session used
 * to put the whole surface into "answering": the composer showed Stop instead
 * of Send, so the learner could not say anything, and nothing was streaming
 * for them to stop. The idle watchdog could not rescue it either, because
 * loading a session stamps `updatedAt` with the load time, which makes a
 * four-day-old turn look freshly active on every tick.
 *
 * Recency is what separates the two cases. A turn that really is running was
 * touched moments ago — the same signal the idle watchdog already trusts, so
 * this reuses its window (Settings › Network) rather than inventing a second
 * notion of "too quiet". Past that window, the status is stale bookkeeping
 * rather than a live turn, and the honest local answer is `idle`: we do not
 * know whether it finished or failed, only that it is not happening now.
 */
export function resolveLoadedRunStatus<T extends string>(
  status: T,
  lastActivityAt: number,
  now: number,
  idleTimeoutMs: number,
): T | "idle" {
  if (status !== "running") return status;
  // No usable timestamp: trust the server rather than guessing.
  if (!Number.isFinite(lastActivityAt) || lastActivityAt <= 0) return status;
  if (now - lastActivityAt <= idleTimeoutMs) return status;
  return "idle";
}
