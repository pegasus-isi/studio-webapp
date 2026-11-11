/**
 * Did this reply run out of output budget? (#665)
 *
 * A provider stops a reply at its output-token cap and OpenCode records that
 * as `finish: "length"`. That is not an error: `info.error` stays unset, so
 * the panel's error box never fires. When the cap is reached while the model
 * is still reasoning, the reply has no text and no tool call, and the turn
 * rendered as nothing at all — the chat looked like it had done nothing.
 *
 * Seen live (2026-11-10): the gateway's default model routes mostly to a
 * backend capped at 8192 output tokens, reasoning included, and two first
 * turns spent all of it thinking (`output: 0, reasoning: 8192`).
 *
 * Kept out of the component so the rule can be tested without a DOM.
 */

import type { Part } from "@opencode-ai/sdk";

export interface OutputLimitInfo {
  /**
   * "empty": stopped before saying or doing anything — the case that used to
   * render as nothing. "truncated": stopped after some text, so what is shown
   * is only the start of the reply.
   */
  kind: "empty" | "truncated";
  /** Tokens spent reasoning in this step, when the provider reported them. */
  reasoning: number;
  /** Tokens of visible output in this step. */
  output: number;
}

interface MessageLike {
  role: string;
  finish?: string;
  error?: unknown;
  time: { completed?: number };
  tokens?: { output?: number; reasoning?: number };
}

/**
 * `null` unless `info` is a finished assistant step that the provider cut off
 * at its output limit.
 *
 * A step with a tool call is not "empty" even with no text: the tool is what
 * it did, and the next step carries on from it. A step with an error is left
 * to the error box, which already says what happened.
 */
export function outputLimitInfo(
  info: MessageLike,
  parts: readonly Part[]
): OutputLimitInfo | null {
  if (info.role !== "assistant") return null;
  if (info.finish !== "length") return null;
  if (info.error) return null;
  if (info.time.completed === undefined) return null;

  const said = parts.some(
    (part) => part.type === "text" && part.text.trim().length > 0
  );
  const acted = parts.some((part) => part.type === "tool");

  return {
    kind: said || acted ? "truncated" : "empty",
    reasoning: info.tokens?.reasoning ?? 0,
    output: info.tokens?.output ?? 0,
  };
}

/** "8192" → "8.2K"; small numbers as they are. */
export function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  const k = n / 1000;
  return `${k >= 100 ? Math.round(k) : k.toFixed(1).replace(/\.0$/, "")}K`;
}
