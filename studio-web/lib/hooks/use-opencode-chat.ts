"use client";

/**
 * The OpenCode-backed chat turn (#367).
 *
 * OpenCode owns the conversation: a prompt is fired with `prompt_async`, the
 * turn's whole life arrives on the event stream, and the transcript is read
 * back from the server. Nothing here has to survive a navigation for the
 * answer to survive one, which is why this engine needs no equivalent of
 * services/chat_turn.py.
 *
 * The reducer keeps parts and message envelopes separately and joins them at
 * render, because either can arrive first: a part event names its message id,
 * and an event can land before the `message.updated` that introduces it. Both
 * are ordered by id — OpenCode's ids are monotonic — so an out-of-order or
 * replayed event lands in the right place instead of at the end.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";
import type { Message, Part } from "@opencode-ai/sdk";
import * as oc from "@/lib/opencode/client";
import type {
  PermissionReply,
  PermissionRequest,
  QuestionRequest,
} from "@/lib/opencode/client";
import {
  EMPTY,
  reduce,
  selectMessages,
  type RenderedMessage,
} from "@/lib/opencode/transcript";

export type { RenderedMessage };
import { routeAgent } from "@/lib/opencode/auto-agent";
import { subscribeEvents, type RawEvent } from "@/lib/opencode/events";
import * as api from "@/lib/api/client";
import { useChatStore } from "@/lib/stores/chat-store";
import { useChatContextStore } from "@/lib/stores/chat-context-store";
import { useNotebookContextStore } from "@/lib/stores/notebook-context-store";
import { useOpenCodeStore } from "@/lib/stores/opencode-store";

/** `properties` of an event, as an object we can read fields off. */
function props(event: RawEvent): Record<string, unknown> {
  return (event.properties ?? {}) as Record<string, unknown>;
}

/**
 * A human-readable message from one of OpenCode's error objects.
 *
 * Every variant is `{ name, data: { message } }` — verified against a live
 * 1.18.29 server, where a deliberate stop yields
 * `{"name":"MessageAbortedError","data":{"message":"Aborted"}}`. Reading a
 * top-level `.message` (which the type does not have) silently produced the
 * generic fallback for every real failure: a gateway 401, a context-length
 * error, all of them.
 */
export function errorMessage(value: unknown): string {
  if (!value) return "The model returned an error";
  if (typeof value === "string") return value;
  const obj = value as { message?: unknown; name?: unknown; data?: unknown };
  const data = obj.data as { message?: unknown } | undefined;
  if (data && typeof data.message === "string") return data.message;
  // Tolerated for the other direction: OpenCode's v2 payloads put `message` at
  // the top level, and this reader is cheap insurance if the v1 shape follows.
  if (typeof obj.message === "string") return obj.message;
  if (typeof obj.name === "string") return obj.name;
  return "The model returned an error";
}

/** True for the error a user's own Stop button produces. */
export function isAbortError(value: unknown): boolean {
  return (value as { name?: unknown } | null)?.name === "MessageAbortedError";
}

/**
 * Session creations in flight, by conversation.
 *
 * Module-level, and shared by every hook instance in this document, because
 * the effect below can run twice for one conversation: React's development
 * strict mode double-invokes effects, and a remount does the same. Without
 * this, one conversation gets two sessions and the second wins — the first
 * being an empty session left on the server for no reason.
 *
 * It does not help across documents (the JupyterLab iframe is its own), where
 * a simultaneous first mount can still mint two. Harmless: an OpenCode session
 * costs nothing until it is used, and each document keeps the one it made.
 */
const creating = new Map<string, Promise<string>>();

export function useOpenCodeChat() {
  const convId = useChatStore((s) => s.activeId);
  const sessionID = useOpenCodeStore((s) => s.sessionByConv[convId]);
  const agent = useOpenCodeStore((s) => s.agentByConv[convId]);
  const setSession = useOpenCodeStore((s) => s.setSession);
  const setAgentForConv = useOpenCodeStore((s) => s.setAgent);
  const clearSession = useOpenCodeStore((s) => s.clearSession);

  const [state, dispatch] = useReducer(reduce, EMPTY);
  /**
   * The directory this session is rooted in, as the server reports it.
   *
   * Every session is created without one now, so this is the engine's own
   * default (the workspace root) — but it is still read back rather than
   * assumed. Agents,
   * skills and commands all resolve per directory, so anything describing what
   * this session can use has to ask about *this* one, and a session created
   * before that default changed keeps the directory it was made with.
   */
  const [sessionDirectory, setSessionDirectory] = useState<string | undefined>();
  /**
   * Whether we know the session's directory.
   *
   * Tracked separately from the value, because "we could not ask" and "the
   * engine's default" are indistinguishable once both are `undefined` — and
   * that is the state where the capability lists are wrong while looking
   * entirely normal, describing $HOME instead of the workflow. A caller that
   * cannot see the difference cannot warn about it or offer to retry.
   */
  const [directoryStatus, setDirectoryStatus] = useState<
    "none" | "loading" | "ok" | "error"
  >("none");

  // The session the effects are allowed to act on. Read from a ref inside the
  // stream handler so a session switch can't have events from the old session
  // landing in the new transcript.
  const sessionRef = useRef<string | undefined>(sessionID);
  sessionRef.current = sessionID;

  // Bumped to ask for another attempt at creating a session. The effect below
  // has no other reason to re-run once it has failed, so without this a panel
  // that opened while the engine was still starting stayed dead until a full
  // page reload — including after the "Restart the engine" button had brought
  // it back.
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  // No `directory` is passed at session creation, deliberately. The engine
  // joins a *relative* path onto its session's directory, and the always-on
  // knowledge tells every model to write `workflows/<name>/…`, so a session
  // rooted anywhere but the workspace root makes that string mean the wrong
  // place: rooted at the workflow's own folder the same string doubles into
  // …/workflows/<name>/workflows/<name>/. Rooting every session at the engine's
  // default — the workspace root, see docker/supervisor/scripts/opencode.sh —
  // makes one relative path mean one place in every conversation.
  //
  // What this gives up is that a workflow-scoped chat no longer starts *inside*
  // that workflow's folder. It costs one path segment: the Active Context block
  // already tells the model which workflow the conversation is about, and
  // `workflows/<name>/…` is the form the knowledge teaches anyway.
  //
  // It also removes a race rather than working around one. The session used to
  // wait for the project list, because a directory fixed at creation could not
  // be corrected afterwards; there is nothing left to wait for.

  // Ensure this conversation has a session.
  useEffect(() => {
    if (sessionID) return;
    let cancelled = false;
    let pending = creating.get(convId);
    if (!pending) {
      pending = oc
        .createSession({ title: "Studio chat" })
        .then((session) => session.id)
        .finally(() => creating.delete(convId));
      creating.set(convId, pending);
    }
    pending.then(
      // Recorded even if this component is gone: the pairing belongs to the
      // conversation, not to a mounted panel, and dropping it on unmount left
      // an unused session behind and made the next mount create another.
      (id) => setSession(convId, id),
      (e: Error) => {
        if (!cancelled) dispatch({ kind: "error", error: e.message });
      }
    );
    return () => {
      cancelled = true;
    };
  }, [sessionID, convId, setSession, attempt]);

  // Reads are ordered by hand. Two reconnects in quick succession put two
  // transcript reads in flight, and the older one resolving last would apply an
  // older snapshot over a newer one.
  const loadSeq = useRef(0);
  // The same ordering problem for the pending-approval snapshots: two
  // reconnects can put two in flight, and an older one landing last would put
  // a resolved approval's card back on screen.
  const syncSeq = useRef(0);
  const statusSeq = useRef(0);

  useEffect(() => {
    if (!sessionID) {
      setSessionDirectory(undefined);
      setDirectoryStatus("none");
      return;
    }
    let cancelled = false;
    setDirectoryStatus("loading");
    oc.getSession(sessionID)
      .then((session) => {
        if (cancelled) return;
        setSessionDirectory(session.directory);
        setDirectoryStatus("ok");
      })
      .catch(() => {
        // Undefined means the catalogs fall back to the engine's own default
        // directory — a worse answer than the session's, but a usable one.
        //
        // Retried on `attempt` for the same reason session creation is: the
        // likeliest time for this to fail is a panel opening while the engine
        // is still starting, and without a retry the mistake is permanent —
        // every capability list would describe $HOME for the rest of the
        // session, quietly and plausibly.
        if (cancelled) return;
        setSessionDirectory(undefined);
        setDirectoryStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [sessionID, attempt]);

  const load = useCallback(
    async (id: string) => {
      const seq = (loadSeq.current += 1);
      try {
        const messages = await oc.getMessages(id);
        if (sessionRef.current !== id || seq !== loadSeq.current) return;
        dispatch({ kind: "loaded", messages });
      } catch (e) {
        // A session id that no longer exists on the server (a cleared OpenCode
        // database, a session deleted elsewhere). Drop the pairing so the
        // effect above mints a new one, rather than showing an error the user
        // can do nothing about.
        const message = (e as Error).message || "";
        if (/session/i.test(message) && /(not found|invalid)/i.test(message)) {
          clearSession(convId);
          dispatch({ kind: "reset" });
          return;
        }
        dispatch({ kind: "error", error: message });
      }
    },
    [convId, clearSession]
  );

  /**
   * Reconcile what the engine is blocked on against what we are showing.
   *
   * Permissions and questions are only announced through events, so anything
   * raised while the stream was down has no card, and anything resolved while
   * it was down has a card that no longer means anything. Both strand the
   * user: the first leaves a turn stopped with nothing to click, the second
   * leaves a button that does nothing.
   *
   * The lists are the truth, so this replaces rather than adds — see
   * `reconcile` in transcript.ts for how a request raised *after* the snapshot
   * survives it.
   *
   * A failure here used to be swallowed, which is the worst of the three
   * outcomes: the turn is blocked, no card is shown, and nothing says so. So
   * it retries, and then reports.
   */
  const syncPending = useCallback(
    async (id: string, signal: AbortSignal) => {
      const stillOurs = () => !signal.aborted && sessionRef.current === id;

      for (let attemptNumber = 0; attemptNumber < 3; attemptNumber += 1) {
        const since = Date.now();
        // Numbered per fetch, not per call: a retry issued after a later call
        // started is still the newest request for the data, and the reducer
        // drops whichever snapshot is overtaken.
        const seq = (syncSeq.current += 1);
        try {
          const [permissions, questions] = await Promise.all([
            oc.listPermissions(),
            oc.listQuestions(),
          ]);
          if (!stillOurs()) return;
          dispatch({
            kind: "permissions-synced",
            requests: permissions.filter((r) => r.sessionID === id),
            since,
            seq,
          });
          dispatch({
            kind: "questions-synced",
            requests: questions.filter((r) => r.sessionID === id),
            since,
            seq,
          });
          return;
        } catch {
          if (!stillOurs()) return;
          // 300ms, 600ms. The engine restarting is the common cause and it
          // comes back quickly; a longer wait would just extend the window in
          // which a pending dialog is invisible.
          await new Promise((resolve) =>
            setTimeout(resolve, 300 * 2 ** attemptNumber)
          );
        }
      }

      if (!stillOurs()) return;
      dispatch({
        kind: "error",
        error:
          "Could not check whether the assistant is waiting on an approval. " +
          "If the reply seems stuck, reload or restart the engine.",
      });
    },
    []
  );

  // Load the transcript, then follow it. Re-reading on every (re)connection is
  // what makes a dropped stream harmless: whatever happened while we were
  // disconnected is in OpenCode, and this asks for it.
  useEffect(() => {
    // Reset first: switching to a conversation that has no session yet used to
    // return here and leave the previous conversation's transcript rendered
    // under the new one's title, for as long as it took to create a session —
    // or forever, if that failed.
    dispatch({ kind: "reset" });
    if (!sessionID) return;
    const controller = new AbortController();

    subscribeEvents({
      signal: controller.signal,
      onOpen: () => {
        dispatch({ kind: "connection", connection: "open" });
        void load(sessionID);

        // Whether this session is busy right now. `session.idle` is an event,
        // so a turn that finished while the stream was down leaves nothing to
        // observe: without this the composer stays on Stop and refuses to send
        // for the rest of the session. Absent from the map means idle.
        //
        // Sequenced for the same reason the transcript read and the pending
        // snapshots are: two reconnects put two reads in flight, and an older
        // one landing last would reinstate a "busy" the turn has since left —
        // which is the stuck composer this call exists to prevent.
        const statusRequest = (statusSeq.current += 1);
        void oc
          .getSessionStatus()
          .then((statuses) => {
            if (controller.signal.aborted) return;
            if (statusRequest !== statusSeq.current) return;
            const status = statuses?.[sessionID];
            dispatch({ kind: "busy", busy: (status?.type ?? "idle") !== "idle" });
          })
          .catch(() => undefined);

        void syncPending(sessionID, controller.signal);
      },
      onError: () => dispatch({ kind: "connection", connection: "retrying" }),
      onEvent: (event) => {
        const p = props(event);
        const forThisSession = (id: unknown) => id === sessionRef.current;

        switch (event.type) {
          case "message.updated": {
            const info = p.info as Message | undefined;
            if (info && forThisSession(info.sessionID)) {
              dispatch({ kind: "message", info });
            }
            break;
          }
          case "message.part.updated": {
            const part = p.part as Part | undefined;
            if (part && forThisSession(part.sessionID)) {
              dispatch({ kind: "part", part });
            }
            break;
          }
          case "message.removed": {
            if (forThisSession(p.sessionID)) {
              dispatch({
                kind: "message-removed",
                messageID: String(p.messageID),
              });
            }
            break;
          }
          case "message.part.delta": {
            if (!forThisSession(p.sessionID)) break;
            if (typeof p.delta !== "string") break;
            dispatch({
              kind: "delta",
              messageID: String(p.messageID),
              partID: String(p.partID),
              field: String(p.field ?? "text"),
              delta: p.delta,
            });
            break;
          }
          case "message.part.removed": {
            if (forThisSession(p.sessionID)) {
              dispatch({
                kind: "part-removed",
                messageID: String(p.messageID),
                partID: String(p.partID),
              });
            }
            break;
          }
          case "session.status": {
            if (!forThisSession(p.sessionID)) break;
            const status = p.status as { type?: string } | undefined;
            dispatch({ kind: "busy", busy: status?.type !== "idle" });
            break;
          }
          case "session.idle": {
            if (forThisSession(p.sessionID)) {
              dispatch({ kind: "busy", busy: false });
              // An idle session is waiting on nothing, so any card still
              // showing is stale: its request was resolved some other way —
              // a sibling answered "always" with a pattern that covered it,
              // Stop aborted the turn, the engine restarted — and the
              // `permission.replied` for it never reached us. Re-read the
              // lists rather than clearing blindly, so a request raised a
              // moment later is not lost with them.
              if (pendingRef.current > 0 && sessionRef.current) {
                void syncPending(sessionRef.current, controller.signal);
              }
            }
            break;
          }
          case "session.error": {
            // sessionID is optional on this event: a provider-level failure
            // isn't always attributable to a session, and a 401 from the
            // gateway is exactly the error the user most needs to see.
            if (p.sessionID && !forThisSession(p.sessionID)) break;
            dispatch({ kind: "error", error: errorMessage(p.error) });
            dispatch({ kind: "busy", busy: false });
            break;
          }
          case "permission.asked": {
            const request = event.properties as PermissionRequest;
            if (forThisSession(request?.sessionID)) {
              dispatch({ kind: "permission", request });
            }
            break;
          }
          case "permission.replied": {
            const id = (p.permissionID ?? p.id) as string | undefined;
            if (id) dispatch({ kind: "permission-done", id });
            break;
          }
          case "question.asked": {
            const request = event.properties as QuestionRequest;
            if (forThisSession(request?.sessionID)) {
              dispatch({ kind: "question", request });
            }
            break;
          }
          case "question.replied":
          case "question.rejected": {
            const id = (p.id ?? p.requestID) as string | undefined;
            if (id) dispatch({ kind: "question-done", id });
            break;
          }
          default:
            // Every other event type — plugin.added, catalog.updated, a type
            // added in a future release — is not ours to interpret.
            break;
        }
      },
    });

    return () => controller.abort();
  }, [sessionID, load, syncPending]);

  const messages: RenderedMessage[] = useMemo(
    () => selectMessages(state),
    [state]
  );
  // For send, which is memoised on convId alone and so would otherwise see the
  // transcript as it was when the conversation was opened.
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  const setAgent = useCallback(
    (next: string) => setAgentForConv(convId, next),
    [convId, setAgentForConv]
  );

  const send = useCallback(
    async (
      text: string,
      model?: { providerID: string; modelID: string },
      // Agents the engine offers. Needed only in Auto mode, which must not
      // route to an agent this engine does not have.
      available?: readonly string[]
    ) => {
      const id = sessionRef.current;
      if (!id || !text.trim()) return;
      dispatch({ kind: "error", error: null });
      // This transcript lives in the OpenCode session, not the chat store, so
      // addMessage's auto-title never ran and every conversation stayed
      // "New chat" in the switcher. Only the user's own text names it — the
      // synthetic context parts below are not what they asked.
      useChatStore.getState().titleFromMessage(convId, text);
      // Optimistic: the user pressed send, and the first event can be a second
      // away while a reasoning model thinks. `session.status` corrects it.
      dispatch({ kind: "busy", busy: true });

      const parts: { type: "text"; text: string; synthetic?: boolean }[] = [
        { type: "text", text },
      ];

      // The focused notebook, when the panel is the one JupyterLab iframes and
      // the user ticked the context box. A synthetic part rather than an
      // addition to their sentence: the builtin engine hides it by sending a
      // separate API copy of the message, and this transcript has no such
      // copy — the message stored is the message shown.
      const { activeNotebookPath, include } =
        useNotebookContextStore.getState();
      if (activeNotebookPath && include) {
        parts.push({
          type: "text",
          text:
            `[Active Jupyter notebook: ${activeNotebookPath} — when I refer ` +
            `to "this notebook", read this file with your tools.]`,
          synthetic: true,
        });
      }

      // The Active Context block, re-resolved per turn so a run that has since
      // failed is described as failed. Sent as a synthetic part: the model sees
      // it, the renderer collapses it, and it costs no backend involvement in
      // the turn itself.
      const activeScope = useChatContextStore.getState().scopeByConv[convId];
      let scopeBlock: string | null = null;
      if (activeScope) {
        try {
          const { block } = await api.getChatScopeBlock(
            activeScope.type,
            activeScope.id
          );
          if (block) {
            scopeBlock = block;
            parts.push({ type: "text", text: block, synthetic: true });
          }
        } catch {
          // Context is an enhancement; a failed lookup must not eat the turn.
        }
      }

      try {
        // The agent is read at send time rather than captured, so switching to
        // plan mode applies to the next turn without re-rendering anything.
        // No stored agent is Auto: the specialist is chosen per message, from
        // the words and the run in context (lib/opencode/auto-agent.ts).
        let selected: string | undefined =
          useOpenCodeStore.getState().agentByConv[convId] || undefined;
        if (!selected && available) {
          const history = messagesRef.current;
          let previous: string | null = null;
          for (let i = history.length - 1; i >= 0; i--) {
            const info = history[i].info;
            if (info.role === "assistant") {
              previous = info.mode;
              break;
            }
          }
          selected = routeAgent({ text, scopeBlock, previous, available }).agent;
        }
        await oc.promptAsync(id, { parts, model, agent: selected });
        // A turn sent while the chat is about a run is the user asking the AI
        // about it — whichever way the scope got there (the dashboard's button,
        // the chip, following the Workflows page). The server links it to that
        // run's failure episode, and 404s a run that has none, which is the
        // common case and not an error.
        if (activeScope?.type === "run") {
          void api
            .markEpisodeAsked(activeScope.id, { conversation_id: id, turn: true })
            .catch(() => undefined);
        }
      } catch (e) {
        dispatch({ kind: "error", error: (e as Error).message });
        dispatch({ kind: "busy", busy: false });
      }
    },
    [convId]
  );

  const stop = useCallback(async () => {
    const id = sessionRef.current;
    if (!id) return;
    try {
      await oc.abortSession(id);
    } catch (e) {
      dispatch({ kind: "error", error: (e as Error).message });
    }
    dispatch({ kind: "busy", busy: false });
  }, []);

  // Each reply clears its card first, so a live-looking dialog can't be
  // clicked twice while the request settles — and puts it back if the request
  // failed, because the engine is still blocked on that answer and a card the
  // user can no longer see is a turn that never resumes.
  // How many cards are up, readable from the event handler (which closes over
  // refs, not state) so an idle session can tell whether there is anything
  // stale to reconcile.
  const pendingRef = useRef(0);
  pendingRef.current = state.permissions.length + state.questions.length;

  // Why a failed reply does not always bring the card back.
  //
  // It used to, unconditionally: a card whose reply failed was re-added so
  // the user could try again. For a transient failure that is right. For
  // "Permission request not found" it is a trap — the engine has already
  // resolved that request (a sibling card answered "always" with a pattern
  // that covered it, Stop aborted the turn, the engine restarted), so every
  // retry fails the same way and the card can never be dismissed. Seen on
  // the first day of early-user testing with three bash cards stacked up.
  // A request the engine no longer holds is done, whatever the user clicked;
  // the card stays gone, and the lists are re-read so any other card in the
  // same state goes with it.
  const requestGone = (e: unknown) => /not found/i.test((e as Error).message ?? "");
  const resync = () => {
    if (sessionRef.current) {
      void syncPending(sessionRef.current, new AbortController().signal);
    }
  };

  const answerPermission = useCallback(
    async (requestID: string, reply: PermissionReply) => {
      const request = state.permissions.find((p) => p.id === requestID);
      dispatch({ kind: "permission-done", id: requestID });
      try {
        await oc.replyPermission(requestID, reply);
      } catch (e) {
        if (requestGone(e)) {
          resync();
          return;
        }
        dispatch({ kind: "error", error: (e as Error).message });
        if (request) dispatch({ kind: "permission", request });
      }
    },
    [state.permissions]
  );

  const answerQuestion = useCallback(
    async (requestID: string, answers: string[][]) => {
      const request = state.questions.find((q) => q.id === requestID);
      dispatch({ kind: "question-done", id: requestID });
      try {
        await oc.replyQuestion(requestID, answers);
      } catch (e) {
        if (requestGone(e)) {
          resync();
          return;
        }
        dispatch({ kind: "error", error: (e as Error).message });
        if (request) dispatch({ kind: "question", request });
      }
    },
    [state.questions]
  );

  const rejectQuestion = useCallback(
    async (requestID: string) => {
      const request = state.questions.find((q) => q.id === requestID);
      dispatch({ kind: "question-done", id: requestID });
      try {
        await oc.rejectQuestion(requestID);
      } catch (e) {
        if (requestGone(e)) {
          resync();
          return;
        }
        dispatch({ kind: "error", error: (e as Error).message });
        if (request) dispatch({ kind: "question", request });
      }
    },
    [state.questions]
  );

  return {
    sessionID,
    messages,
    busy: state.busy,
    error: state.error,
    permissions: state.permissions,
    questions: state.questions,
    connection: state.connection,
    sessionDirectory,
    directoryStatus,
    agent,
    setAgent,
    retry,
    send,
    stop,
    answerPermission,
    answerQuestion,
    rejectQuestion,
  };
}
