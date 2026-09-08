/**
 * The transcript state machine for the OpenCode engine (#367).
 *
 * Pure on purpose: given the events OpenCode emits, this decides what the
 * panel shows. Keeping it out of the hook means the ordering rules — which
 * arrive out of order, which are deltas, which supersede a re-read — can be
 * reasoned about and replayed against a recorded stream without a browser.
 *
 * Two rules earn their keep, both from the #367 spike:
 *
 * - **Parts and message envelopes are stored separately** and joined at
 *   render, because either can arrive first: a part event names its message id
 *   and can land before the `message.updated` that introduces that message.
 * - **Text arrives as deltas, not as whole parts.** A 1600-character reply
 *   produced 284 `message.part.delta` events and one `message.part.updated`
 *   with the finished text. Consuming only whole parts leaves the panel blank
 *   until the turn ends.
 *
 * Ordering is by id throughout. OpenCode's ids are monotonic, so a replayed or
 * out-of-order event lands in its place in the transcript rather than at the
 * end.
 */

import type { Message, Part } from "@opencode-ai/sdk";
import type {
  MessageWithParts,
  PermissionRequest,
  QuestionRequest,
} from "@/lib/opencode/client";

/**
 * Part fields a delta must never touch: they say what the part *is*, and the
 * renderer registry and the ordering both key off them.
 */
const IDENTITY_FIELDS = new Set(["id", "type", "sessionID", "messageID"]);

export interface RenderedMessage {
  info: Message;
  parts: Part[];
}

export type Connection = "connecting" | "open" | "retrying";

export interface State {
  infos: Record<string, Message>;
  /** messageID -> partID -> part. */
  parts: Record<string, Record<string, Part>>;
  busy: boolean;
  error: string | null;
  permissions: PermissionRequest[];
  questions: QuestionRequest[];
  /**
   * When each pending request was first held, by request id.
   *
   * Only used to reconcile against a server snapshot: a request that arrived
   * *after* the snapshot was asked for is not missing from it, it is newer
   * than it, and must survive the reconciliation.
   */
  pendingSince: Record<string, number>;
  /**
   * Sequence number of the most recent pending-request snapshot applied.
   *
   * Snapshots are ordered by hand because two reconnects in quick succession
   * put two of them in flight: without this, a newer snapshot that correctly
   * cleared a resolved request could be followed by an older one that still
   * listed it, putting the dead card back on screen. Permissions and questions
   * are always fetched together and share the counter.
   */
  syncSeq: number;
  connection: Connection;
}

export type Action =
  | { kind: "reset" }
  | { kind: "loaded"; messages: MessageWithParts[] }
  | { kind: "message"; info: Message }
  | { kind: "part"; part: Part }
  | {
      kind: "delta";
      messageID: string;
      partID: string;
      field: string;
      delta: string;
    }
  | { kind: "part-removed"; messageID: string; partID: string }
  | { kind: "message-removed"; messageID: string }
  | { kind: "busy"; busy: boolean }
  | { kind: "error"; error: string | null }
  | { kind: "permission"; request: PermissionRequest }
  | { kind: "permission-done"; id: string }
  | { kind: "question"; request: QuestionRequest }
  | { kind: "question-done"; id: string }
  /**
   * The server's full set of pending requests for this session, as of
   * `since`. Replaces what we hold rather than adding to it, so a request
   * resolved while the stream was down stops being offered.
   */
  | {
      kind: "permissions-synced";
      requests: PermissionRequest[];
      /** When the snapshot was asked for, to date requests against. */
      since: number;
      /** Monotonic per fetch, so an older snapshot cannot land last. */
      seq: number;
    }
  | {
      kind: "questions-synced";
      requests: QuestionRequest[];
      since: number;
      seq: number;
    }
  | { kind: "connection"; connection: Connection };

export const EMPTY: State = {
  infos: {},
  parts: {},
  busy: false,
  error: null,
  permissions: [],
  questions: [],
  pendingSince: {},
  syncSeq: 0,
  connection: "connecting",
};

export function reduce(state: State, action: Action): State {
  switch (action.kind) {
    case "reset":
      return { ...EMPTY, connection: state.connection };
    case "loaded": {
      // Merge, never replace.
      //
      // A read is a snapshot taken before it landed, so anything created while
      // it was in flight is missing from it — most obviously the prompt the
      // user sent a moment ago. Replacing `infos` with the response deleted
      // that message, and OpenCode does not re-announce a settled one, so it
      // stayed gone for the rest of the session and the reply read as an
      // answer to nothing. Removals arrive as `message.removed` events and on
      // the next reset, so merging does not leave deleted messages behind.
      const infos = { ...state.infos };
      const parts = { ...state.parts };
      for (const m of action.messages) {
        infos[m.info.id] = m.info;
        const held = parts[m.info.id] ?? {};
        const merged: Record<string, Part> = { ...held };
        for (const part of m.parts) {
          merged[part.id] = mergePart(held[part.id], part);
        }
        parts[m.info.id] = merged;
      }
      return { ...state, infos, parts, error: null };
    }
    case "message":
      return {
        ...state,
        infos: { ...state.infos, [action.info.id]: action.info },
      };
    case "part": {
      const messageID = action.part.messageID;
      return {
        ...state,
        parts: {
          ...state.parts,
          [messageID]: {
            ...(state.parts[messageID] ?? {}),
            [action.part.id]: action.part,
          },
        },
      };
    }
    case "delta": {
      // Append to the part we hold. A delta for a part that has not been
      // introduced yet is dropped rather than synthesised: OpenCode sends the
      // empty part first, and the trailing `message.part.updated` carries the
      // complete text anyway, so a dropped chunk self-corrects at the end of
      // the turn instead of inventing a part with no type.
      const held = state.parts[action.messageID]?.[action.partID];
      if (!held) return state;
      // A part's identity is not something a delta may grow. `field` names a
      // field to append to and today is always "text", but these are strings
      // too: a delta naming `type` would rewrite it to "text!" and the part
      // would fall out of the renderer registry as unsupported content.
      if (IDENTITY_FIELDS.has(action.field)) return state;
      const current = (held as unknown as Record<string, unknown>)[action.field];
      if (typeof current !== "string") return state;
      const next = { ...held, [action.field]: current + action.delta } as Part;
      return {
        ...state,
        parts: {
          ...state.parts,
          [action.messageID]: {
            ...state.parts[action.messageID],
            [action.partID]: next,
          },
        },
      };
    }
    case "message-removed": {
      // A reverted or deleted turn. Handled live as well as by the re-read on
      // reconnect, or a message the server no longer has would stay on screen
      // for the rest of the session.
      if (!(action.messageID in state.infos)) return state;
      const infos = { ...state.infos };
      delete infos[action.messageID];
      const parts = { ...state.parts };
      delete parts[action.messageID];
      return { ...state, infos, parts };
    }
    case "part-removed": {
      const held = state.parts[action.messageID];
      if (!held) return state;
      const next = { ...held };
      delete next[action.partID];
      return { ...state, parts: { ...state.parts, [action.messageID]: next } };
    }
    case "busy":
      return { ...state, busy: action.busy };
    case "error":
      return { ...state, error: action.error };
    case "permission":
      return state.permissions.some((p) => p.id === action.request.id)
        ? state
        : {
            ...state,
            permissions: [...state.permissions, action.request],
            pendingSince: {
              ...state.pendingSince,
              [action.request.id]: Date.now(),
            },
          };
    case "permission-done":
      return {
        ...state,
        permissions: state.permissions.filter((p) => p.id !== action.id),
        pendingSince: without(state.pendingSince, action.id),
      };
    case "question":
      return state.questions.some((q) => q.id === action.request.id)
        ? state
        : {
            ...state,
            questions: [...state.questions, action.request],
            pendingSince: {
              ...state.pendingSince,
              [action.request.id]: Date.now(),
            },
          };
    case "question-done":
      return {
        ...state,
        questions: state.questions.filter((q) => q.id !== action.id),
        pendingSince: without(state.pendingSince, action.id),
      };
    case "permissions-synced": {
      // An overtaken snapshot is not news. Applying it would resurrect a card
      // a newer snapshot had already cleared.
      if (action.seq < state.syncSeq) return state;
      const [permissions, pendingSince] = reconcile(
        state.permissions,
        action.requests,
        state.pendingSince,
        action.since
      );
      return {
        ...state,
        permissions,
        pendingSince,
        syncSeq: Math.max(state.syncSeq, action.seq),
      };
    }
    case "questions-synced": {
      if (action.seq < state.syncSeq) return state;
      const [questions, pendingSince] = reconcile(
        state.questions,
        action.requests,
        state.pendingSince,
        action.since
      );
      return {
        ...state,
        questions,
        pendingSince,
        syncSeq: Math.max(state.syncSeq, action.seq),
      };
    }
    case "connection":
      return { ...state, connection: action.connection };
  }
}

/** The transcript, in order, with each message's parts in order. */
export function selectMessages(state: State): RenderedMessage[] {
  return Object.values(state.infos)
    .sort(byId)
    .map((info) => ({
      info,
      parts: Object.values(state.parts[info.id] ?? {}).sort(byId),
    }));
}

function without(
  map: Record<string, number>,
  id: string
): Record<string, number> {
  if (!(id in map)) return map;
  const next = { ...map };
  delete next[id];
  return next;
}

/**
 * Reconcile held pending requests against the server's snapshot.
 *
 * Appending the snapshot is not enough: a request answered while the stream
 * was down is gone from the server and its `replied` event was never seen, so
 * a card for it would stay on screen and do nothing when clicked. Replacing
 * outright is not enough either — a request raised *after* the snapshot was
 * asked for is newer than it, not absent from it, and dropping it would block
 * the turn with no dialog at all.
 *
 * So: the snapshot is the truth for everything that existed when it was taken,
 * and anything held since then is kept.
 */
function reconcile<T extends { id: string }>(
  held: T[],
  snapshot: T[],
  pendingSince: Record<string, number>,
  since: number
): [T[], Record<string, number>] {
  const snapshotIds = new Set(snapshot.map((r) => r.id));
  const newer = held.filter(
    (r) => !snapshotIds.has(r.id) && (pendingSince[r.id] ?? 0) >= since
  );
  const next = [...snapshot, ...newer];
  const nextSince: Record<string, number> = {};
  for (const request of next) {
    nextSince[request.id] = pendingSince[request.id] ?? since;
  }
  // Ids belonging to the other kind of request are left alone: one map serves
  // both, and a permission sync must not forget when a question arrived.
  for (const [id, at] of Object.entries(pendingSince)) {
    const wasOurs =
      held.some((r) => r.id === id) || snapshotIds.has(id);
    if (!wasOurs) nextSince[id] = at;
  }
  return [next, nextSince];
}

function byId(a: { id: string }, b: { id: string }): number {
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/**
 * Reconcile a part we hold against the same part from a re-read.
 *
 * The read is authoritative except for text still streaming: reconnecting five
 * seconds into a twenty-second reply gives a snapshot shorter than what the
 * deltas have already built, and taking it wholesale makes the reply jump
 * backwards and then lose the characters the next delta appends past the
 * shorter value. Keeping the longer text is right in both directions — the
 * finished part from the server is always at least as long as what we have.
 */
function mergePart(held: Part | undefined, read: Part): Part {
  if (!held || held.type !== read.type) return read;
  if (
    (read.type === "text" || read.type === "reasoning") &&
    "text" in held &&
    typeof held.text === "string" &&
    held.text.length > read.text.length
  ) {
    return { ...read, text: held.text };
  }
  return read;
}
