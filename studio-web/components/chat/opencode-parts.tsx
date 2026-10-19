"use client";

/**
 * One renderer per OpenCode message part type, plus a fallback (#367).
 *
 * The registry is keyed by the part's `type` string and every lookup can miss:
 * a part type Studio has never seen still renders, collapsed, and the turn
 * completes. That is the property that lets OpenCode ship a new part type
 * without a change here — and it is why the fallback is a real renderer with
 * the raw payload in it, not a silent `return null`.
 *
 * A part always arrives whole — each `message.part.updated` carries the entire
 * part — but text does NOT arrive that way in practice: the full part lands
 * once, at the end, and the reply is built from `message.part.delta` events in
 * between (see docs/OPENCODE-CHAT-SPIKE.md). `lib/opencode/transcript.ts`
 * applies those deltas, so by the time a part reaches a renderer here it is a
 * complete object either way. Do not conclude from that that the delta path is
 * redundant; deleting it makes every reply appear only when it finishes.
 */

import { Component, createContext, useContext, useEffect, useState } from "react";
import type { Part } from "@opencode-ai/sdk";
import { ChatMarkdown } from "@/components/chat/markdown";
import { cn } from "@/lib/utils";

/** Narrow a part to one variant by its type tag. */
type PartOf<T extends Part["type"]> = Extract<Part, { type: T }>;

function Collapsed({
  label,
  tone = "muted",
  children,
  open = false,
}: {
  label: React.ReactNode;
  tone?: "muted" | "error";
  children: React.ReactNode;
  open?: boolean;
}) {
  return (
    <details
      open={open}
      className={cn(
        "rounded border text-xs",
        tone === "error"
          ? "border-rose-500/40 bg-rose-500/10"
          : "border-line bg-muted"
      )}
    >
      <summary className="cursor-pointer px-2 py-1 text-fgmuted">
        {label}
      </summary>
      <div className="px-2 pb-1.5">{children}</div>
    </details>
  );
}

function Pre({
  children,
  height = "tall",
}: {
  children: string;
  height?: "tall" | "short";
}) {
  return (
    <pre
      className={cn(
        "overflow-auto whitespace-pre-wrap text-fgmuted [overflow-wrap:anywhere]",
        // The panel is ~400px wide and shares its height with the composer, so
        // a scroll region has to leave room for the conversation around it.
        // Reasoning is the long one — model monologue, hundreds of lines — and
        // at the tall setting an expanded block filled the whole pane, leaving
        // the answer it belongs to off screen behind two nested scrollbars.
        height === "short" ? "max-h-40" : "max-h-64"
      )}
    >
      {children}
    </pre>
  );
}

function json(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

function TextPartView({ part }: { part: PartOf<"text"> }) {
  // A synthetic part is context Studio injected (the Active Context block);
  // `ignored` marks a part the server left out of the model's view. Both are
  // real parts of the transcript, and neither is something the user wrote or
  // the model said, so neither is shown as prose — an ignored part rendered as
  // conversation is text presented as part of an exchange it was never in.
  if (!part.text.trim()) return null;
  if (part.synthetic || part.ignored) {
    return (
      <Collapsed
        label={
          part.synthetic
            ? "Context sent with this message"
            : "Not sent to the model"
        }
      >
        <Pre>{part.text}</Pre>
      </Collapsed>
    );
  }
  return <ChatMarkdown content={part.text} />;
}

/** "42s", "3m 05s". */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

/**
 * Milliseconds since `start`, re-rendering once a second while `live`.
 *
 * The tick is the point: a model can reason for minutes without emitting
 * anything the transcript would otherwise redraw for, and a counter that
 * moves is the cheapest proof the turn has not hung.
 */
export function useElapsed(start: number | undefined, live: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [live]);
  return start === undefined ? 0 : now - start;
}

/** A dot that pulses while something is in progress. */
export function LiveDot() {
  return (
    <span className="relative mr-1.5 inline-flex h-2 w-2 align-middle">
      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-pegasus-500 opacity-60" />
      <span className="relative inline-flex h-2 w-2 rounded-full bg-pegasus-500" />
    </span>
  );
}

/**
 * Whether the message these parts belong to can still grow. A part's own
 * `time.end` is not enough: a turn that is stopped or dies in an error leaves
 * an open reasoning part open forever, and without this its counter would tick
 * on under a reply that has already ended. Defaults to false, so a part
 * rendered outside a live turn (history, a reload) never looks active.
 */
export const MessageLiveContext = createContext(false);

function lastLine(text: string): string {
  const lines = text.trimEnd().split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (line) return line;
  }
  return "";
}

function ReasoningPartView({ part }: { part: PartOf<"reasoning"> }) {
  // `time.end` is unset until the reasoning block closes. While it is open the
  // block stays collapsed (expanded it buries the reply under the monologue)
  // but must not look idle: a static "Reasoning" label over minutes of model
  // thought reads as a hung chat. So the label counts, and the newest line of
  // the reasoning shows underneath, changing as the model writes.
  const live = useContext(MessageLiveContext) && part.time?.end === undefined;
  const elapsed = useElapsed(part.time?.start, live);
  if (!part.text.trim()) return null;
  const duration =
    part.time?.end !== undefined && part.time.start !== undefined
      ? part.time.end - part.time.start
      : undefined;
  const label = live ? (
    <>
      <LiveDot />
      Thinking · {formatElapsed(elapsed)}
    </>
  ) : duration !== undefined && duration >= 1000 ? (
    `Thought for ${formatElapsed(duration)}`
  ) : (
    "Reasoning"
  );
  return (
    <div>
      <Collapsed label={label}>
        <Pre height="short">{part.text}</Pre>
      </Collapsed>
      {live && (
        <div className="mt-1 truncate px-2 text-xs italic text-fgsubtle">
          {lastLine(part.text)}
        </div>
      )}
    </div>
  );
}

const TOOL_STATUS_LABEL: Record<string, string> = {
  pending: "queued",
  running: "running",
  completed: "done",
  error: "failed",
};

function ToolPartView({ part }: { part: PartOf<"tool"> }) {
  const [showInput, setShowInput] = useState(false);
  const state = part.state;
  const status = state.status;
  const title = status === "completed" || status === "running" ? state.title : undefined;

  return (
    <div
      className={cn(
        "rounded border px-2 py-1.5 text-xs",
        status === "error"
          ? "border-rose-500/40 bg-rose-500/10"
          : "border-line bg-muted"
      )}
    >
      <div className="flex items-center gap-1.5">
        <span className="font-mono font-medium text-fg">{part.tool}</span>
        <span className="text-fgsubtle">
          {TOOL_STATUS_LABEL[status] ?? status}
        </span>
        {status === "running" && (
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-pegasus-500" />
        )}
        <button
          onClick={() => setShowInput((v) => !v)}
          className="ml-auto text-fgsubtle hover:text-fg"
          aria-label={showInput ? "Hide arguments" : "Show arguments"}
        >
          {showInput ? "hide args" : "args"}
        </button>
      </div>
      {title && (
        <div
          // Wrapped, not truncated. The title of a bash call *is* the command,
          // and `mkdir -p $HOME/work/workflows/…` cut off at the pane's width
          // hid the part that says what it actually did. Capped at three lines
          // so a pathological one-liner still can't take over the transcript;
          // the full text is in the tooltip and in `args`.
          className="mt-0.5 line-clamp-3 font-mono text-xs leading-snug text-fgmuted [overflow-wrap:anywhere]"
          title={title}
        >
          {title}
        </div>
      )}
      {showInput && <Pre>{json("input" in state ? state.input : {})}</Pre>}
      {status === "completed" && state.output ? (
        <div className="mt-1">
          <Collapsed label="Output">
            <Pre>{state.output}</Pre>
          </Collapsed>
        </div>
      ) : null}
      {status === "error" && (
        <div className="mt-1 text-rose-600 dark:text-rose-400">
          <Pre>{state.error}</Pre>
        </div>
      )}
    </div>
  );
}

function CompactionPartView({ part }: { part: PartOf<"compaction"> }) {
  // The visible marker the acceptance criteria ask for: a session that passed
  // the model's context window keeps going, and says where the summary happened
  // rather than appearing to forget for no reason.
  return (
    <div className="flex items-center gap-3 py-1" role="separator">
      <div className="h-px flex-1 bg-line" />
      <span className="text-2xs font-semibold uppercase tracking-wider text-fgsubtle">
        {part.auto ? "Context summarized to fit" : "Context summarized"}
      </span>
      <div className="h-px flex-1 bg-line" />
    </div>
  );
}

function FilePartView({ part }: { part: PartOf<"file"> }) {
  const name = part.filename || part.url.split("/").pop() || part.mime;
  return (
    <div className="inline-flex items-center gap-1.5 rounded border border-line bg-muted px-2 py-1 text-xs text-fgmuted">
      <span aria-hidden>📄</span>
      <span className="truncate" title={part.url}>
        {name}
      </span>
    </div>
  );
}

function PatchPartView({ part }: { part: PartOf<"patch"> }) {
  return (
    <Collapsed
      label={`Changed ${part.files.length} file${part.files.length === 1 ? "" : "s"}`}
    >
      <Pre>{part.files.join("\n")}</Pre>
    </Collapsed>
  );
}

function AgentPartView({ part }: { part: PartOf<"agent"> }) {
  return (
    <div className="text-xs text-fgmuted">
      Handled by <span className="font-medium text-fg">{part.name}</span>
    </div>
  );
}

function SubtaskPartView({ part }: { part: PartOf<"subtask"> }) {
  return (
    <Collapsed label={`Subtask: ${part.description || part.agent}`}>
      <Pre>{part.prompt}</Pre>
    </Collapsed>
  );
}

function RetryPartView({ part }: { part: PartOf<"retry"> }) {
  return (
    <div className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs text-amber-700 dark:text-amber-400">
      Retrying after an error (attempt {part.attempt})
    </div>
  );
}

/** Renders nothing: a real decision, not a missing case. */
function Invisible() {
  return null;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type PartRenderer = (props: { part: any }) => React.ReactElement | null;

/**
 * type -> renderer. `step-start`, `step-finish` and `snapshot` are turn
 * bookkeeping: step-finish's tokens and cost are shown once in the message
 * footer instead of twice per step, and a snapshot is a git ref nobody reads.
 */
const RENDERERS: Record<string, PartRenderer> = {
  text: TextPartView,
  reasoning: ReasoningPartView,
  tool: ToolPartView,
  compaction: CompactionPartView,
  file: FilePartView,
  patch: PatchPartView,
  agent: AgentPartView,
  subtask: SubtaskPartView,
  retry: RetryPartView,
  "step-start": Invisible,
  "step-finish": Invisible,
  snapshot: Invisible,
};

function RawPartView({ part }: { part: Part }) {
  const Renderer = RENDERERS[part.type];
  if (Renderer) return <Renderer part={part} />;
  // An OpenCode version newer than this UI. Show that something happened and
  // what it was, rather than dropping it — a hidden part is how a transcript
  // starts making no sense.
  return (
    <Collapsed label={`Unsupported content (${part.type})`}>
      <Pre>{json(part)}</Pre>
    </Collapsed>
  );
}

/**
 * Contains a render failure to the one part that caused it.
 *
 * The registry's fallback covers a part type this UI has never seen. It does
 * nothing for a *known* type whose shape changed — a renderer dereferencing a
 * field that is suddenly absent throws during render, and with no boundary
 * anywhere in Studio that takes down the whole page, not just the transcript.
 * The promise this file makes is that an unrecognised part still renders and
 * the turn completes; this is what makes that true of malformed parts too.
 */
class PartBoundary extends Component<
  { part: Part; children: React.ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error) {
    // Reported once per part, not swallowed: a part that stopped rendering is
    // a version mismatch worth seeing in the console.
    console.error("opencode part failed to render", this.props.part, error);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <Collapsed label={`Could not display this ${this.props.part.type}`}>
        <Pre>{json(this.props.part)}</Pre>
      </Collapsed>
    );
  }
}

export function PartView({ part }: { part: Part }) {
  return (
    <PartBoundary part={part}>
      <RawPartView part={part} />
    </PartBoundary>
  );
}
