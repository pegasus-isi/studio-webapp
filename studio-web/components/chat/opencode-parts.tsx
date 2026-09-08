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
 * Parts arrive whole (see docs/OPENCODE-CHAT-SPIKE.md): each `message.part.updated`
 * event carries the entire part, so a live turn and a transcript re-read from
 * the server produce the same objects and these components need no notion of
 * streaming.
 */

import { useState } from "react";
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

function Pre({ children }: { children: string }) {
  return (
    <pre className="max-h-64 overflow-auto whitespace-pre-wrap text-fgmuted [overflow-wrap:anywhere]">
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
  // A synthetic part is context Studio injected (the Active Context block), and
  // `ignored` marks a part the server excluded from the model's view. Both are
  // real parts of the transcript; neither is something the user wrote or the
  // model said, so neither is shown as prose.
  if (part.synthetic) {
    return (
      <Collapsed label="Context sent with this message">
        <Pre>{part.text}</Pre>
      </Collapsed>
    );
  }
  if (!part.text.trim()) return null;
  return <ChatMarkdown content={part.text} />;
}

function ReasoningPartView({ part }: { part: PartOf<"reasoning"> }) {
  if (!part.text.trim()) return null;
  return (
    <Collapsed label="Reasoning">
      <Pre>{part.text}</Pre>
    </Collapsed>
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
        <div className="mt-0.5 truncate text-fgmuted" title={title}>
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
      <span className="text-[10px] font-medium uppercase tracking-wider text-fgsubtle">
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

export function PartView({ part }: { part: Part }) {
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
