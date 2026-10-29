"use client";

import { useState } from "react";
import { Check, ChevronDown, Loader2, X } from "lucide-react";

import type { ChatMsg } from "@/lib/stores/chat-store";
import {
  latestToolActivity,
  mergeToolActivity,
  toolActivityTarget,
  type ToolActivity,
} from "@/lib/chat/tool-activity";
import { cn } from "@/lib/utils";

const VISIBLE_TOOLS = 5;

export function ToolActivityList({ msg }: { msg: ChatMsg }) {
  const entries = mergeToolActivity(msg.toolCalls, msg.toolResults);
  const [showAll, setShowAll] = useState(false);

  if (entries.length === 0) return null;

  const visible = showAll
    ? entries
    : latestToolActivity(entries, VISIBLE_TOOLS);
  const hidden = entries.length - visible.length;

  return (
    <div className="mt-2 border-t border-line pt-2">
      <div className="mb-1 flex items-center justify-between text-[10px] uppercase tracking-wide text-fgsubtle">
        <span>
          Tools · {entries.length} call{entries.length === 1 ? "" : "s"}
        </span>
        {entries.length > VISIBLE_TOOLS && (
          <button
            type="button"
            onClick={() => setShowAll((v) => !v)}
            className="normal-case text-fgsubtle hover:text-fg"
          >
            {showAll ? "Show latest 5" : `Show all ${entries.length}`}
          </button>
        )}
      </div>

      <div className="space-y-1">
        {visible.map((entry) => (
          <ToolActivityRow key={entry.id} entry={entry} />
        ))}
      </div>

      {hidden > 0 && !showAll && (
        <div className="mt-1 text-[10px] text-fgsubtle">
          {hidden} earlier call{hidden === 1 ? "" : "s"} hidden
        </div>
      )}
    </div>
  );
}

function ToolActivityRow({ entry }: { entry: ToolActivity }) {
  const target = toolActivityTarget(entry.name, entry.arguments);

  return (
    <details
      className={cn(
        "group rounded border border-line bg-muted text-xs",
        entry.status === "error" && "border-rose-500/40"
      )}
    >
      <summary className="flex cursor-pointer items-center gap-1.5 px-2 py-1 text-fgmuted">
        <StatusIcon status={entry.status} />
        <span className="shrink-0 font-mono font-medium text-fg">
          {entry.name}
        </span>
        {target && (
          <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-fgsubtle">
            {target}
          </span>
        )}
        <ChevronDown className="ml-auto h-3 w-3 shrink-0 transition-transform group-open:rotate-180" />
      </summary>
      {entry.result !== undefined ? (
        <pre className="max-h-40 overflow-auto px-2 pb-1 text-fgmuted">
          {entry.result}
        </pre>
      ) : (
        <div className="px-2 pb-1 text-fgsubtle">Running…</div>
      )}
    </details>
  );
}

function StatusIcon({ status }: { status: ToolActivity["status"] }) {
  if (status === "running") {
    return <Loader2 className="h-3 w-3 animate-spin text-pegasus-500" />;
  }
  if (status === "error") {
    return <X className="h-3 w-3 text-rose-600" />;
  }
  return <Check className="h-3 w-3 text-emerald-600" />;
}
