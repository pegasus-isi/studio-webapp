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

import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
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
import { subscribeEvents, type RawEvent } from "@/lib/opencode/events";
import * as api from "@/lib/api/client";
import { useChatStore } from "@/lib/stores/chat-store";
import { useChatContextStore } from "@/lib/stores/chat-context-store";
import { useNotebookContextStore } from "@/lib/stores/notebook-context-store";
import { useOpenCodeStore } from "@/lib/stores/opencode-store";
import { useWorkflowProjects } from "@/lib/hooks/use-workflows";

/** `properties` of an event, as an object we can read fields off. */
function props(event: RawEvent): Record<string, unknown> {
  return (event.properties ?? {}) as Record<string, unknown>;
}

function errorMessage(value: unknown): string {
  if (!value) return "The model returned an error";
  if (typeof value === "string") return value;
  const obj = value as { message?: unknown; name?: unknown; data?: unknown };
  if (typeof obj.message === "string") return obj.message;
  const data = obj.data as { message?: unknown } | undefined;
  if (data && typeof data.message === "string") return data.message;
  if (typeof obj.name === "string") return obj.name;
  return "The model returned an error";
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
  const scope = useChatContextStore((s) => s.scopeByConv[convId] ?? null);
  const sessionID = useOpenCodeStore((s) => s.sessionByConv[convId]);
  const setSession = useOpenCodeStore((s) => s.setSession);
  const clearSession = useOpenCodeStore((s) => s.clearSession);
  const { data: projectData } = useWorkflowProjects();

  const [state, dispatch] = useReducer(reduce, EMPTY);

  // The session the effects are allowed to act on. Read from a ref inside the
  // stream handler so a session switch can't have events from the old session
  // landing in the new transcript.
  const sessionRef = useRef<string | undefined>(sessionID);
  sessionRef.current = sessionID;

  const projectDir = useMemo(() => {
    if (scope?.type !== "workflow") return undefined;
    return projectData?.projects.find((p) => p.project_id === scope.id)
      ?.project_dir;
  }, [scope, projectData]);

  // Ensure this conversation has a session. The workflow's own folder becomes
  // the session directory, which is what roots OpenCode's file tools, its
  // AGENTS.md lookup and its skill discovery — a chat about a workflow should
  // be able to read that workflow without being told where it lives.
  useEffect(() => {
    if (sessionID) return;
    let cancelled = false;
    let pending = creating.get(convId);
    if (!pending) {
      pending = oc
        .createSession({ title: "Studio chat", directory: projectDir })
        .then((session) => session.id)
        .finally(() => creating.delete(convId));
      creating.set(convId, pending);
    }
    pending.then(
      (id) => {
        if (!cancelled) setSession(convId, id);
      },
      (e: Error) => {
        if (!cancelled) dispatch({ kind: "error", error: e.message });
      }
    );
    return () => {
      cancelled = true;
    };
  }, [sessionID, convId, projectDir, setSession]);

  const load = useCallback(
    async (id: string) => {
      try {
        const messages = await oc.getMessages(id);
        if (sessionRef.current !== id) return;
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

  // Load the transcript, then follow it. Re-reading on every (re)connection is
  // what makes a dropped stream harmless: whatever happened while we were
  // disconnected is in OpenCode, and this asks for it.
  useEffect(() => {
    if (!sessionID) return;
    dispatch({ kind: "reset" });
    const controller = new AbortController();

    subscribeEvents({
      signal: controller.signal,
      onOpen: () => {
        dispatch({ kind: "connection", connection: "open" });
        void load(sessionID);
        // Permissions asked while we were away are still pending and their
        // event is long gone; the turn stays stopped until one is answered.
        void oc
          .listPermissions()
          .then((pending) => {
            for (const request of pending) {
              if (request.sessionID === sessionID) {
                dispatch({ kind: "permission", request });
              }
            }
          })
          .catch(() => undefined);
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
  }, [sessionID, load]);

  const messages: RenderedMessage[] = useMemo(
    () => selectMessages(state),
    [state]
  );

  const send = useCallback(
    async (text: string, model?: { providerID: string; modelID: string }) => {
      const id = sessionRef.current;
      if (!id || !text.trim()) return;
      dispatch({ kind: "error", error: null });
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
      if (activeScope) {
        try {
          const { block } = await api.getChatScopeBlock(
            activeScope.type,
            activeScope.id
          );
          if (block) parts.push({ type: "text", text: block, synthetic: true });
        } catch {
          // Context is an enhancement; a failed lookup must not eat the turn.
        }
      }

      try {
        await oc.promptAsync(id, { parts, model });
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

  const answerPermission = useCallback(
    async (requestID: string, reply: PermissionReply) => {
      // Clear the card immediately: the reply endpoint is idempotent enough
      // that a duplicate is harmless, and leaving a live-looking dialog up
      // while the request settles invites a second click.
      dispatch({ kind: "permission-done", id: requestID });
      try {
        await oc.replyPermission(requestID, reply);
      } catch (e) {
        dispatch({ kind: "error", error: (e as Error).message });
      }
    },
    []
  );

  const answerQuestion = useCallback(
    async (requestID: string, answers: string[][]) => {
      dispatch({ kind: "question-done", id: requestID });
      try {
        await oc.replyQuestion(requestID, answers);
      } catch (e) {
        dispatch({ kind: "error", error: (e as Error).message });
      }
    },
    []
  );

  const rejectQuestion = useCallback(async (requestID: string) => {
    dispatch({ kind: "question-done", id: requestID });
    try {
      await oc.rejectQuestion(requestID);
    } catch (e) {
      dispatch({ kind: "error", error: (e as Error).message });
    }
  }, []);

  return {
    sessionID,
    messages,
    busy: state.busy,
    error: state.error,
    permissions: state.permissions,
    questions: state.questions,
    connection: state.connection,
    send,
    stop,
    answerPermission,
    answerQuestion,
    rejectQuestion,
  };
}
