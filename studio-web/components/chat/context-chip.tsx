"use client";

/**
 * The context chip above the chat input: shows what the conversation is
 * scoped to (a workflow, a run, or nothing) and lets the user change it.
 *
 * Auto-follows the Workflows page — opening a run there scopes the chat to it
 * — until the user picks explicitly, which pins the conversation (see
 * chat-context-store). Lists are fetched lazily when the dropdown opens; the
 * chip itself costs nothing.
 */

import { useEffect, useRef, useState } from "react";
import * as api from "@/lib/api/client";
import { genId, useChatStore } from "@/lib/stores/chat-store";
import {
  useChatContextStore,
  type ChatScope,
} from "@/lib/stores/chat-context-store";
import { useWorkflowStore } from "@/lib/stores/workflow-store";
import { cn } from "@/lib/utils";

// One in-flight/settled fetch per list per page load — the dropdown reuses it
// and auto-follow label resolution rides the same promise. The workflows scan
// walks the filesystem server-side; opening the dropdown twice shouldn't walk
// it twice.
let _runsPromise: Promise<api.WorkflowRun[]> | null = null;
let _projectsPromise: Promise<api.WorkflowProject[]> | null = null;

function fetchRuns(): Promise<api.WorkflowRun[]> {
  if (!_runsPromise) {
    _runsPromise = api
      .getWorkflows()
      .then((r) => r.workflows)
      .catch(() => {
        _runsPromise = null; // let a later open retry
        return [];
      });
  }
  return _runsPromise;
}

function fetchProjects(): Promise<api.WorkflowProject[]> {
  if (!_projectsPromise) {
    _projectsPromise = api
      .getWorkflowProjects()
      .then((r) => r.projects)
      .catch(() => {
        _projectsPromise = null;
        return [];
      });
  }
  return _projectsPromise;
}

function runLabel(run: { name?: string; run_name?: string; run_id: string }) {
  const wf = run.name || "";
  const dir = run.run_name || "";
  if (wf && dir && wf !== dir) return `${wf} / ${dir}`;
  return dir || wf || run.run_id.slice(0, 8);
}

export function ChatContextChip() {
  const activeId = useChatStore((s) => s.activeId);
  const addMessage = useChatStore((s) => s.addMessage);
  const scope = useChatContextStore((s) => s.scopeByConv[activeId] ?? null);
  const pinned = useChatContextStore((s) => s.pinnedByConv[activeId] ?? false);
  const setScope = useChatContextStore((s) => s.setScope);
  const followSelection = useChatContextStore((s) => s.followSelection);
  const resetToFollow = useChatContextStore((s) => s.resetToFollow);

  const selectedRunId = useWorkflowStore((s) => s.selectedRunId);
  const selectedProjectId = useWorkflowStore((s) => s.selectedProjectId);

  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [runs, setRuns] = useState<api.WorkflowRun[] | null>(null);
  const [projects, setProjects] = useState<api.WorkflowProject[] | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => setMounted(true), []);

  // Close on outside click, same idiom as the conversation dropdown.
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node))
        setOpen(false);
    };
    if (open) {
      document.addEventListener("mousedown", onClick);
      return () => document.removeEventListener("mousedown", onClick);
    }
  }, [open]);

  // Auto-follow the Workflows page selection. The label may resolve late (the
  // id is known before any list is fetched); followSelection refreshes it once
  // the list arrives, and never overrides a pinned conversation.
  useEffect(() => {
    if (selectedProjectId) {
      followSelection(activeId, {
        type: "workflow",
        id: selectedProjectId,
        label: selectedProjectId,
      });
    } else if (selectedRunId) {
      followSelection(activeId, {
        type: "run",
        id: selectedRunId,
        label: selectedRunId.slice(0, 8),
      });
      fetchRuns().then((list) => {
        const run = list.find((r) => r.run_id === selectedRunId);
        if (run) {
          followSelection(activeId, {
            type: "run",
            id: selectedRunId,
            label: runLabel(run),
          });
        }
      });
    }
    // Leaving the page (both null) keeps the last-followed scope: the chat is
    // usually *about* what the user just looked at.
  }, [selectedRunId, selectedProjectId, activeId, followSelection]);

  const openDropdown = () => {
    setOpen((v) => !v);
    fetchProjects().then(setProjects);
    fetchRuns().then(setRuns);
  };

  // Announce explicit context switches inside the transcript so a scroll-back
  // reads correctly — answers above a divider were given under the old scope.
  const announce = (label: string) => {
    if (useChatStore.getState().messages.length > 0) {
      addMessage({ id: genId(), role: "system", content: label });
    }
  };

  const pick = (next: ChatScope | null) => {
    setOpen(false);
    const same =
      (next === null && scope === null) ||
      (next !== null &&
        scope !== null &&
        next.type === scope.type &&
        next.id === scope.id);
    if (same) return;
    setScope(activeId, next);
    announce(next ? `Context: ${next.label}` : "Context cleared");
  };

  // SSR renders no scope (localStorage is client-only); render the neutral
  // chip until mounted to avoid a hydration mismatch.
  const shown = mounted ? scope : null;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={openDropdown}
        title={
          shown
            ? `Chat is scoped to this ${shown.type} — click to change`
            : "Scope the chat to a workflow or run"
        }
        data-testid="chat-context-chip"
        className={cn(
          "flex max-w-[16rem] items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors",
          shown
            ? "border-pegasus-400 bg-pegasus-50 text-pegasus-700 dark:bg-pegasus-400/10 dark:text-pegasus-300"
            : "border-line text-fgmuted hover:border-pegasus-300 hover:text-fg"
        )}
      >
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
          {shown?.type === "run" ? (
            <polygon points="5 3 19 12 5 21 5 3" />
          ) : shown?.type === "workflow" ? (
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
          ) : (
            <>
              <circle cx="12" cy="12" r="10" />
              <line x1="2" y1="12" x2="22" y2="12" />
              <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
            </>
          )}
        </svg>
        <span className="truncate">
          {shown ? shown.label : "No context"}
        </span>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && (
        <div
          className="absolute bottom-8 left-0 z-50 max-h-80 w-72 overflow-auto rounded-lg border border-line bg-surface py-1 shadow-lg"
          data-testid="chat-context-dropdown"
        >
          <button
            onClick={() => pick(null)}
            className={cn(
              "flex w-full items-center px-3 py-1.5 text-left text-xs hover:bg-muted",
              !shown ? "font-medium text-fg" : "text-fgmuted"
            )}
          >
            No context
          </button>
          {pinned && (
            <button
              onClick={() => {
                setOpen(false);
                resetToFollow(activeId);
              }}
              className="flex w-full items-center px-3 py-1.5 text-left text-xs text-fgmuted hover:bg-muted"
              title="Let the chat follow whatever workflow or run you open"
            >
              Follow my selection
            </button>
          )}

          <div className="border-t border-line px-3 pb-0.5 pt-1.5 text-[10px] font-semibold uppercase tracking-wider text-fgsubtle">
            Workflows
          </div>
          {projects === null ? (
            <div className="px-3 py-1.5 text-xs text-fgsubtle">Loading…</div>
          ) : projects.length === 0 ? (
            <div className="px-3 py-1.5 text-xs text-fgsubtle">
              No workflows yet
            </div>
          ) : (
            projects.map((p) => (
              <button
                key={p.project_id}
                onClick={() =>
                  pick({ type: "workflow", id: p.project_id, label: p.name })
                }
                className={cn(
                  "flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-xs hover:bg-muted",
                  shown?.type === "workflow" && shown.id === p.project_id
                    ? "font-medium text-fg"
                    : "text-fgmuted"
                )}
              >
                <span className="truncate">{p.name}</span>
                <span className="shrink-0 text-[10px] text-fgsubtle">
                  {p.status}
                </span>
              </button>
            ))
          )}

          <div className="border-t border-line px-3 pb-0.5 pt-1.5 text-[10px] font-semibold uppercase tracking-wider text-fgsubtle">
            Runs
          </div>
          {runs === null ? (
            <div className="px-3 py-1.5 text-xs text-fgsubtle">Loading…</div>
          ) : runs.length === 0 ? (
            <div className="px-3 py-1.5 text-xs text-fgsubtle">
              No runs yet
            </div>
          ) : (
            runs.map((r) => (
              <button
                key={r.run_id}
                onClick={() =>
                  pick({ type: "run", id: r.run_id, label: runLabel(r) })
                }
                className={cn(
                  "flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-xs hover:bg-muted",
                  shown?.type === "run" && shown.id === r.run_id
                    ? "font-medium text-fg"
                    : "text-fgmuted"
                )}
              >
                <span className="truncate">{runLabel(r)}</span>
                <span className="shrink-0 text-[10px] text-fgsubtle">
                  {r.status}
                </span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
