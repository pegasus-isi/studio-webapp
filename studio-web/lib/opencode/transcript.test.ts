/**
 * The transcript state machine's rules, which are not guessable from the code.
 *
 * Each of these encodes something measured against a live OpenCode server or
 * found by review, and every one of them was a bug first:
 *
 * - text arrives as deltas, not whole parts, so consuming only whole parts
 *   leaves the panel blank until a turn ends;
 * - a transcript re-read is a snapshot from before it landed, so replacing
 *   state with it deleted the prompt the user had just sent;
 * - a pending approval resolved during an outage leaves a card that does
 *   nothing, and one raised after a snapshot must survive that snapshot.
 */

import { describe, expect, it } from "vitest";
import type { Message, Part } from "@opencode-ai/sdk";
import type { PermissionRequest, QuestionRequest } from "@/lib/opencode/client";
import { EMPTY, reduce, selectMessages, type State } from "@/lib/opencode/transcript";

function user(id: string): Message {
  return {
    id,
    sessionID: "ses_1",
    role: "user",
    time: { created: 1 },
    agent: "build",
    model: { providerID: "p", modelID: "m" },
  };
}

function assistant(id: string): Message {
  return {
    id,
    sessionID: "ses_1",
    role: "assistant",
    time: { created: 2 },
    parentID: "msg_parent",
    modelID: "m",
    providerID: "p",
    mode: "build",
    path: { cwd: "/", root: "/" },
    cost: 0,
    tokens: { input: 0, output: 0, reasoning: 0, cache: { read: 0, write: 0 } },
  };
}

function text(id: string, messageID: string, body: string): Part {
  return {
    id,
    sessionID: "ses_1",
    messageID,
    type: "text",
    text: body,
  };
}

function permission(id: string): PermissionRequest {
  return {
    id,
    sessionID: "ses_1",
    permission: "bash",
    patterns: ["echo hi"],
    metadata: { command: "echo hi" },
    always: ["echo *"],
  };
}

function question(id: string): QuestionRequest {
  return {
    id,
    sessionID: "ses_1",
    questions: [{ question: "Which?", header: "Pick", options: [{ label: "a" }] }],
  };
}

function apply(state: State, ...actions: Parameters<typeof reduce>[1][]): State {
  return actions.reduce(reduce, state);
}

describe("streaming text", () => {
  it("builds a reply from deltas", () => {
    const state = apply(
      EMPTY,
      { kind: "message", info: assistant("msg_1") },
      { kind: "part", part: text("prt_1", "msg_1", "") },
      { kind: "delta", messageID: "msg_1", partID: "prt_1", field: "text", delta: "Hel" },
      { kind: "delta", messageID: "msg_1", partID: "prt_1", field: "text", delta: "lo" }
    );

    const [message] = selectMessages(state);
    expect(message.parts[0]).toMatchObject({ type: "text", text: "Hello" });
  });

  it("ignores a delta for a part it has never seen", () => {
    // Dropped rather than synthesised: a part invented from a delta would have
    // no type, and the trailing whole-part update corrects it anyway.
    const state = apply(EMPTY, {
      kind: "delta",
      messageID: "msg_missing",
      partID: "prt_missing",
      field: "text",
      delta: "x",
    });
    expect(selectMessages(state)).toEqual([]);
  });

  it("ignores a delta naming a field that is not a string", () => {
    const state = apply(
      EMPTY,
      { kind: "message", info: assistant("msg_1") },
      { kind: "part", part: text("prt_1", "msg_1", "keep") },
      { kind: "delta", messageID: "msg_1", partID: "prt_1", field: "type", delta: "!" }
    );
    expect(selectMessages(state)[0].parts[0]).toMatchObject({ type: "text" });
  });
});

describe("ordering", () => {
  it("orders messages and parts by id, whatever order they arrive in", () => {
    const state = apply(
      EMPTY,
      { kind: "message", info: assistant("msg_002") },
      { kind: "message", info: user("msg_001") },
      { kind: "part", part: text("prt_002", "msg_002", "second") },
      { kind: "part", part: text("prt_001", "msg_002", "first") }
    );

    expect(selectMessages(state).map((m) => m.info.id)).toEqual([
      "msg_001",
      "msg_002",
    ]);
    expect(selectMessages(state)[1].parts.map((p) => p.id)).toEqual([
      "prt_001",
      "prt_002",
    ]);
  });
});

describe("re-reading the transcript on reconnect", () => {
  it("keeps a message the read is too old to know about", () => {
    // The read is a snapshot from before it landed. Replacing state with it
    // deleted the prompt just sent — which OpenCode never re-announces — so
    // the reply that followed read as an answer to nothing.
    const state = apply(
      EMPTY,
      { kind: "message", info: user("msg_002") },
      { kind: "part", part: text("prt_002", "msg_002", "my prompt") },
      {
        kind: "loaded",
        messages: [{ info: user("msg_001"), parts: [text("prt_001", "msg_001", "older")] }],
      }
    );

    const ids = selectMessages(state).map((m) => m.info.id);
    expect(ids).toEqual(["msg_001", "msg_002"]);
  });

  it("does not let a stale snapshot shorten text the deltas have built", () => {
    let state = apply(
      EMPTY,
      { kind: "message", info: assistant("msg_1") },
      { kind: "part", part: text("prt_1", "msg_1", "") }
    );
    for (let i = 0; i < 600; i += 1) {
      state = reduce(state, {
        kind: "delta",
        messageID: "msg_1",
        partID: "prt_1",
        field: "text",
        delta: "x",
      });
    }

    state = reduce(state, {
      kind: "loaded",
      messages: [
        { info: assistant("msg_1"), parts: [text("prt_1", "msg_1", "x".repeat(400))] },
      ],
    });

    const part = selectMessages(state)[0].parts[0];
    expect(part.type === "text" && part.text.length).toBe(600);
  });

  it("takes the server's text when it is the longer one", () => {
    const state = apply(
      EMPTY,
      { kind: "message", info: assistant("msg_1") },
      { kind: "part", part: text("prt_1", "msg_1", "half") },
      {
        kind: "loaded",
        messages: [
          { info: assistant("msg_1"), parts: [text("prt_1", "msg_1", "half and whole")] },
        ],
      }
    );

    const part = selectMessages(state)[0].parts[0];
    expect(part.type === "text" && part.text).toBe("half and whole");
  });
});

describe("removals", () => {
  it("drops a message the server says is gone", () => {
    const state = apply(
      EMPTY,
      { kind: "message", info: user("msg_1") },
      { kind: "part", part: text("prt_1", "msg_1", "hi") },
      { kind: "message-removed", messageID: "msg_1" }
    );
    expect(selectMessages(state)).toEqual([]);
  });

  it("ignores a removal for a message it does not hold", () => {
    const state = apply(EMPTY, { kind: "message-removed", messageID: "msg_nope" });
    expect(state).toEqual(EMPTY);
  });
});

describe("reconciling what the engine is blocked on", () => {
  it("drops a request resolved while the stream was down", () => {
    // Its `replied` event was missed, so the card would stay on screen and do
    // nothing when clicked.
    const held = apply(
      EMPTY,
      { kind: "permission", request: permission("per_stale") },
      { kind: "question", request: question("que_stale") }
    );

    const after = apply(
      held,
      { kind: "permissions-synced", requests: [], since: Date.now() + 10, seq: 1 },
      { kind: "questions-synced", requests: [], since: Date.now() + 10, seq: 1 }
    );

    expect(after.permissions).toEqual([]);
    expect(after.questions).toEqual([]);
    expect(after.pendingSince).toEqual({});
  });

  it("keeps a request raised after the snapshot was asked for", () => {
    // Newer than the snapshot, not absent from it. Dropping it would block the
    // turn with no dialog at all.
    const asked = Date.now();
    const state = apply(
      EMPTY,
      { kind: "permission", request: permission("per_new") },
      { kind: "permissions-synced", requests: [], since: asked, seq: 1 }
    );

    expect(state.permissions.map((p) => p.id)).toEqual(["per_new"]);
  });

  it("adds what it never saw without duplicating what it holds", () => {
    const state = apply(
      EMPTY,
      { kind: "permission", request: permission("per_known") },
      {
        kind: "permissions-synced",
        requests: [permission("per_known"), permission("per_missed")],
        since: Date.now() + 10,
        seq: 1,
      }
    );

    expect(state.permissions.map((p) => p.id).sort()).toEqual([
      "per_known",
      "per_missed",
    ]);
  });

  it("does not forget a question while reconciling permissions", () => {
    const held = apply(EMPTY, { kind: "question", request: question("que_1") });
    const at = held.pendingSince["que_1"];

    const after = reduce(held, {
      kind: "permissions-synced",
      requests: [],
      since: Date.now() + 10,
      seq: 1,
    });

    expect(after.questions.map((q) => q.id)).toEqual(["que_1"]);
    expect(after.pendingSince["que_1"]).toBe(at);
  });

  it("forgets a request once it is answered", () => {
    const state = apply(
      EMPTY,
      { kind: "permission", request: permission("per_1") },
      { kind: "permission-done", id: "per_1" }
    );
    expect(state.permissions).toEqual([]);
    expect(state.pendingSince).toEqual({});
  });
});

describe("turn state", () => {
  it("tracks busy and surfaces an error", () => {
    let state = reduce(EMPTY, { kind: "busy", busy: true });
    expect(state.busy).toBe(true);
    state = reduce(state, { kind: "error", error: "gateway said no" });
    expect(state.error).toBe("gateway said no");
    state = reduce(state, { kind: "busy", busy: false });
    expect(state.busy).toBe(false);
  });

  it("keeps the connection state across a reset", () => {
    // A reset is a session switch, not a disconnection: throwing away what we
    // know about the stream would flash "reconnecting" at the user.
    const state = apply(
      EMPTY,
      { kind: "connection", connection: "open" },
      { kind: "message", info: user("msg_1") },
      { kind: "reset" }
    );
    expect(state.connection).toBe("open");
    expect(selectMessages(state)).toEqual([]);
  });
});

describe("overlapping snapshots", () => {
  it("ignores a snapshot overtaken by a newer one", () => {
    // Two reconnects in quick succession put two snapshots in flight. The
    // newer one clears an approval that was answered during the outage; the
    // older one, still listing it, must not put the dead card back.
    const held = apply(EMPTY, {
      kind: "permission",
      request: permission("per_stale"),
    });

    const newer = reduce(held, {
      kind: "permissions-synced",
      requests: [],
      since: Date.now() + 10,
      seq: 7,
    });
    expect(newer.permissions).toEqual([]);

    const olderLandsLast = reduce(newer, {
      kind: "permissions-synced",
      requests: [permission("per_stale")],
      since: Date.now(),
      seq: 6,
    });

    expect(olderLandsLast.permissions).toEqual([]);
    expect(olderLandsLast).toBe(newer);
  });

  it("applies a later snapshot from the same fetch pair", () => {
    // Permissions and questions are fetched together and dispatched with the
    // same seq, so the second must not be rejected as stale.
    const state = apply(
      EMPTY,
      {
        kind: "permissions-synced",
        requests: [permission("per_1")],
        since: Date.now(),
        seq: 3,
      },
      {
        kind: "questions-synced",
        requests: [question("que_1")],
        since: Date.now(),
        seq: 3,
      }
    );

    expect(state.permissions.map((p) => p.id)).toEqual(["per_1"]);
    expect(state.questions.map((q) => q.id)).toEqual(["que_1"]);
  });

  it("starts a new session with no snapshot history", () => {
    // Otherwise a high seq from the previous session would reject the first
    // snapshot of the next one.
    const state = apply(
      EMPTY,
      {
        kind: "permissions-synced",
        requests: [permission("per_1")],
        since: Date.now(),
        seq: 9,
      },
      { kind: "reset" },
      {
        kind: "permissions-synced",
        requests: [permission("per_2")],
        since: Date.now(),
        seq: 1,
      }
    );

    expect(state.permissions.map((p) => p.id)).toEqual(["per_2"]);
  });
});
