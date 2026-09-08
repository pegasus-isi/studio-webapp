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
  | { kind: "connection"; connection: Connection };

export const EMPTY: State = {
  infos: {},
  parts: {},
  busy: false,
  error: null,
  permissions: [],
  questions: [],
  connection: "connecting",
};

export function reduce(state: State, action: Action): State {
  switch (action.kind) {
    case "reset":
      return { ...EMPTY, connection: state.connection };
    case "loaded": {
      const infos: Record<string, Message> = {};
      const parts: Record<string, Record<string, Part>> = {};
      for (const m of action.messages) {
        infos[m.info.id] = m.info;
        parts[m.info.id] = Object.fromEntries(m.parts.map((p) => [p.id, p]));
      }
      // A re-read is the truth, but it can race a live event that arrived
      // first, so keep parts we already hold for messages the read also has.
      for (const [messageID, held] of Object.entries(state.parts)) {
        if (parts[messageID]) parts[messageID] = { ...held, ...parts[messageID] };
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
        : { ...state, permissions: [...state.permissions, action.request] };
    case "permission-done":
      return {
        ...state,
        permissions: state.permissions.filter((p) => p.id !== action.id),
      };
    case "question":
      return state.questions.some((q) => q.id === action.request.id)
        ? state
        : { ...state, questions: [...state.questions, action.request] };
    case "question-done":
      return {
        ...state,
        questions: state.questions.filter((q) => q.id !== action.id),
      };
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

function byId(a: { id: string }, b: { id: string }): number {
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}
