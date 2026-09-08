/**
 * Calls to the OpenCode agent server, through studio-api's verbatim proxy
 * (#367).
 *
 * Every path here is OpenCode's own, unchanged, because the proxy forwards
 * bytes and adds only credentials. `@opencode-ai/sdk` is pinned to the same
 * version as the server in the image and supplies the *types* — Session,
 * Message, Part — so a payload shape change fails `tsc` rather than the panel.
 *
 * Its generated *client* is deliberately not used. It trails its own server:
 * at 1.18.29 it has no method for `POST /permission/{id}/reply` or the
 * question endpoints, and its event union still names `permission.updated`
 * while the server emits `permission.asked`. Half the calls would go through
 * the SDK and half around it, which is exactly the split that makes the next
 * person guess. One thin layer, one convention, and the types still come from
 * the package.
 *
 * URLs go through `lib/urls.ts` like every other Studio request, so the
 * per-user KubeSpawner prefix is handled in one place — and the SDK's own URL
 * joining, which drops path prefixes, never gets the chance to.
 */

import type { Message, Part, Session } from "@opencode-ai/sdk";
import { apiUrl } from "@/lib/urls";

/** A message with its parts, as `GET /session/{id}/message` returns them. */
export interface MessageWithParts {
  info: Message;
  parts: Part[];
}

/**
 * A pending permission request, as the server's `permission.asked` event and
 * `GET /permission` carry it.
 *
 * Declared here rather than imported because the SDK's v1 types still
 * describe the older `permission.updated` shape. Verified against a live
 * 1.18.29 server in docs/OPENCODE-CHAT-SPIKE.md.
 */
export interface PermissionRequest {
  id: string;
  sessionID: string;
  /** The permission being asked for, e.g. "bash". */
  permission: string;
  /** What is being permitted — for bash, the command itself. */
  patterns: string[];
  metadata: Record<string, unknown>;
  /** Patterns a reply of "always" would allow from now on. */
  always: string[];
  tool?: { messageID: string; callID: string };
}

export type PermissionReply = "once" | "always" | "reject";

/** One question the agent is waiting on an answer to. */
export interface QuestionInfo {
  question: string;
  header: string;
  options: { label: string; description?: string }[];
  multiple?: boolean;
  custom?: boolean;
}

export interface QuestionRequest {
  id: string;
  sessionID: string;
  questions: QuestionInfo[];
}

export interface ProviderCatalog {
  providers: {
    id: string;
    name: string;
    models: Record<string, { name?: string }>;
  }[];
  /** providerID -> the model that provider defaults to. */
  default: Record<string, string>;
}

/** Studio's own view of whether the engine is usable. Not an OpenCode payload. */
export interface EngineStatus {
  running: boolean;
  reason?: string | null;
  detail?: string | null;
  version?: string | null;
  port?: number;
}

function url(path: string): string {
  return apiUrl(`opencode/${path.replace(/^\/+/, "")}`);
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url(path), {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    // Two error shapes reach here: studio-api's own {"detail": …} when the
    // engine is unreachable, and OpenCode's {"message": …, "_tag": …} when a
    // request was bad. Surface whichever is present — a bare status code sent
    // one debugging session looking at the proxy when the payload was wrong.
    let message = body;
    try {
      const parsed = JSON.parse(body);
      message = parsed?.detail ?? parsed?.message ?? body;
    } catch {
      /* not JSON — keep the raw text */
    }
    throw new Error(String(message || `Request failed (${res.status})`));
  }
  // 204 on prompt_async and the reply endpoints.
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export function listSessions(): Promise<Session[]> {
  return call<Session[]>("session");
}

export function getSession(sessionID: string): Promise<Session> {
  return call<Session>(`session/${encodeURIComponent(sessionID)}`);
}

/**
 * Create a session, rooted at `directory` when given.
 *
 * The directory is what roots OpenCode's file tools, its AGENTS.md discovery
 * and its skill loading, so a chat about a workflow wants the workflow's own
 * folder — not the directory the server happened to start in.
 */
export function createSession(body: {
  title?: string;
  directory?: string;
}): Promise<Session> {
  const query = body.directory
    ? `?directory=${encodeURIComponent(body.directory)}`
    : "";
  return call<Session>(`session${query}`, {
    method: "POST",
    body: JSON.stringify(body.title ? { title: body.title } : {}),
  });
}

export function deleteSession(sessionID: string): Promise<unknown> {
  return call(`session/${encodeURIComponent(sessionID)}`, { method: "DELETE" });
}

export function getMessages(sessionID: string): Promise<MessageWithParts[]> {
  return call<MessageWithParts[]>(
    `session/${encodeURIComponent(sessionID)}/message`
  );
}

/**
 * Send a prompt without waiting for the turn.
 *
 * `prompt_async` answers 204 immediately and the turn's whole life arrives on
 * the event stream. That is what makes a closed tab or a navigation harmless:
 * the turn is the server's, not the request's, so nothing here has to be kept
 * alive to finish it — and it is why this engine needs no equivalent of
 * services/chat_turn.py.
 */
export function promptAsync(
  sessionID: string,
  body: {
    parts: { type: "text"; text: string; synthetic?: boolean }[];
    model?: { providerID: string; modelID: string };
    agent?: string;
  }
): Promise<void> {
  return call<void>(`session/${encodeURIComponent(sessionID)}/prompt_async`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function abortSession(sessionID: string): Promise<unknown> {
  return call(`session/${encodeURIComponent(sessionID)}/abort`, {
    method: "POST",
  });
}

/**
 * Which sessions are busy right now, keyed by session id.
 *
 * Absent from the map means idle. Needed on reconnect: `session.idle` is an
 * event, so a turn that finished while the stream was down leaves nothing to
 * observe, and a panel that only tracks events would show a composer stuck on
 * Stop for the rest of the session.
 */
export function getSessionStatus(): Promise<
  Record<string, { type: "idle" | "busy" | "retry" }>
> {
  return call<Record<string, { type: "idle" | "busy" | "retry" }>>(
    "session/status"
  );
}

/**
 * Permissions the engine is currently waiting on.
 *
 * `GET /permission` is in the pinned server's own OpenAPI document and its
 * payload was exercised live (docs/OPENCODE-CHAT-SPIKE.md). Note that the
 * SDK's *generated types* describe an older shape for this — `type`/`pattern`
 * where the server sends `permission`/`patterns`/`always` — which is why
 * `PermissionRequest` above is declared here rather than imported.
 */
export function listPermissions(): Promise<PermissionRequest[]> {
  return call<PermissionRequest[]>("permission");
}

export function replyPermission(
  requestID: string,
  reply: PermissionReply
): Promise<void> {
  return call<void>(`permission/${encodeURIComponent(requestID)}/reply`, {
    method: "POST",
    body: JSON.stringify({ reply }),
  });
}

/** One array of chosen option labels per question, in order. */
export function replyQuestion(
  requestID: string,
  answers: string[][]
): Promise<void> {
  return call<void>(`question/${encodeURIComponent(requestID)}/reply`, {
    method: "POST",
    body: JSON.stringify({ answers }),
  });
}

export function rejectQuestion(requestID: string): Promise<void> {
  return call<void>(`question/${encodeURIComponent(requestID)}/reject`, {
    method: "POST",
  });
}

export function getProviders(): Promise<ProviderCatalog> {
  return call<ProviderCatalog>("config/providers");
}

export function getEngineStatus(): Promise<EngineStatus> {
  return call<EngineStatus>("_studio/status");
}

export function restartEngine(): Promise<{ restarted: boolean }> {
  return call<{ restarted: boolean }>("_studio/restart", { method: "POST" });
}

/** The event stream URL, for the subscriber in `lib/opencode/events.ts`. */
export function eventStreamUrl(): string {
  return url("event");
}
