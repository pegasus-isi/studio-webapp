/**
 * The OpenCode event stream (#367).
 *
 * One global SSE stream carries every session's events, so the subscriber
 * filters by session id. Read through `fetch` rather than `EventSource` for
 * the reason `lib/hooks/use-notifications.ts` documents: EventSource cannot
 * see a non-2xx response, and "the engine is down" has to be distinguishable
 * from "nothing has happened yet" — which is the whole difference between a
 * panel that says so and a panel that just sits there.
 *
 * Unknown event types are passed through untouched. The SDK's own union is
 * incomplete against its server (at 1.18.29 it still names
 * `permission.updated` where the server emits `permission.asked`), and more to
 * the point a new OpenCode event must not be able to break a turn. The
 * handlers narrow the few types Studio acts on and ignore the rest.
 */

import type { Message, Part } from "@opencode-ai/sdk";
import { eventStreamUrl } from "@/lib/opencode/client";
import type { PermissionRequest, QuestionRequest } from "@/lib/opencode/client";

/** An event as it comes off the wire: a type, and whatever came with it. */
export interface RawEvent {
  type: string;
  properties?: unknown;
}

export interface MessageUpdatedEvent extends RawEvent {
  type: "message.updated";
  properties: { info: Message };
}

export interface PartUpdatedEvent extends RawEvent {
  type: "message.part.updated";
  properties: { part: Part; delta?: string };
}

/**
 * A field of a part growing by one chunk.
 *
 * This is how text actually streams. A 1600-character reply produced 284 of
 * these and exactly one `message.part.updated` carrying the finished text, so
 * a renderer that only consumed whole parts would sit blank until the turn
 * ended (measured in the #367 spike). `field` names the part field to append
 * to — "text" for both text and reasoning parts.
 */
export interface PartDeltaEvent extends RawEvent {
  type: "message.part.delta";
  properties: {
    sessionID: string;
    messageID: string;
    partID: string;
    field: string;
    delta: string;
  };
}

export interface PartRemovedEvent extends RawEvent {
  type: "message.part.removed";
  properties: { sessionID: string; messageID: string; partID: string };
}

export interface SessionStatusEvent extends RawEvent {
  type: "session.status";
  properties: {
    sessionID: string;
    status: { type: "idle" | "busy" | "retry"; message?: string };
  };
}

export interface SessionErrorEvent extends RawEvent {
  type: "session.error";
  properties: { sessionID?: string; error?: { message?: string } };
}

export interface PermissionAskedEvent extends RawEvent {
  type: "permission.asked";
  properties: PermissionRequest;
}

export interface PermissionRepliedEvent extends RawEvent {
  type: "permission.replied";
  properties: { sessionID: string; permissionID?: string; id?: string };
}

export interface QuestionAskedEvent extends RawEvent {
  type: "question.asked";
  properties: QuestionRequest;
}

export interface QuestionClosedEvent extends RawEvent {
  type: "question.replied" | "question.rejected";
  properties: { sessionID: string; id?: string; requestID?: string };
}

/**
 * Subscribe until `signal` aborts, calling `onEvent` for every event.
 *
 * Reconnects with capped backoff, and reports each connection's outcome
 * through `onStatus` so the panel can say "reconnecting" instead of quietly
 * showing a transcript that has stopped updating. `onOpen` fires on every
 * successful (re)connection, which is the moment to re-read the session:
 * anything that happened while disconnected was missed, and OpenCode holds the
 * truth.
 */
export function subscribeEvents(opts: {
  signal: AbortSignal;
  onEvent: (event: RawEvent) => void;
  onOpen?: () => void;
  onError?: (error: Error) => void;
}): void {
  const { signal, onEvent, onOpen, onError } = opts;
  let attempt = 0;

  const run = async (): Promise<void> => {
    while (!signal.aborted) {
      try {
        const res = await fetch(eventStreamUrl(), {
          signal,
          headers: { Accept: "text/event-stream" },
        });
        if (!res.ok || !res.body) {
          throw new Error(`Event stream failed (${res.status})`);
        }
        onOpen?.();

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!signal.aborted) {
          const { done, value } = await reader.read();
          if (done) break;
          // Reset the backoff only once the connection has actually carried
          // something. Doing it on the response headers instead means a peer
          // that accepts the connection and immediately closes the body —
          // an engine restarting, a proxy hiccup — reconnects every second
          // forever, and each attempt re-reads the whole transcript from
          // `onOpen`.
          attempt = 0;
          // CRLF line endings would otherwise never match the "\n\n" frame
          // separator: no events, and a buffer that grows without bound.
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
          // Frames are separated by a blank line; a partial frame stays in the
          // buffer. Splitting on "\n" instead would hand half a JSON object to
          // JSON.parse on every large tool result.
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";
          for (const frame of frames) {
            // One event per frame, its payload being every `data:` line joined
            // by a newline — which is what SSE specifies and what OpenCode's
            // own client does. Parsing each line as its own event happens to
            // work today only because JSON.stringify emits no newlines; a
            // single event that ever spans two data lines would then fail to
            // parse twice instead of parsing once.
            //
            // Lines that are not `data:` are skipped: ": heartbeat" comments
            // keep the connection open through proxies and carry nothing, and
            // `id:`/`retry:` are not used here.
            const payload = frame
              .split("\n")
              .filter((line) => line.startsWith("data:"))
              .map((line) => line.slice(5).replace(/^ /, ""))
              .join("\n")
              .trim();
            if (!payload) continue;
            try {
              onEvent(JSON.parse(payload) as RawEvent);
            } catch {
              /* an unparseable frame is not worth killing the stream for */
            }
          }
        }
      } catch (e) {
        if (signal.aborted) return;
        onError?.(e as Error);
      }
      if (signal.aborted) return;
      // 1s, 2s, 4s … capped at 15s. The stream ending is normal (a restarted
      // engine, a proxy timeout), so this is a routine path, not an incident.
      attempt += 1;
      const delay = Math.min(1000 * 2 ** (attempt - 1), 15000);
      // Abortable: a torn-down subscription should not hold a timer for up to
      // 15 seconds after the panel is gone.
      await new Promise<void>((resolve) => {
        const timer = setTimeout(finish, delay);
        function finish() {
          clearTimeout(timer);
          signal.removeEventListener("abort", finish);
          resolve();
        }
        signal.addEventListener("abort", finish);
      });
    }
  };

  void run();
}
