"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import dagre from "@dagrejs/dagre";
import "@xyflow/react/dist/style.css";

interface GraphNode {
  id: string;
  label: string;
  transformation?: string | null;
  status: string;
  exec_job_id?: string;
}

interface GraphData {
  name: string;
  nodes: GraphNode[];
  edges: { source: string; target: string }[];
  workflow_status?: string;
}

interface Props {
  graphUrl: string; // /api/workflows/.../graph.json
  title: string;
  live?: boolean; // poll for status updates (run graphs)
  onClose: () => void;
}

// UML-state-machine-style palette (after mjstealey/workflow-visualizer)
const STATUS_STYLE: Record<string, { bg: string; border: string; fg: string }> = {
  unsubmitted: { bg: "#f3f4f6", border: "#d1d5db", fg: "#374151" },
  pending: { bg: "#fef3c7", border: "#f59e0b", fg: "#78350f" },
  queued: { bg: "#fef3c7", border: "#f59e0b", fg: "#78350f" },
  running: { bg: "#cffafe", border: "#06b6d4", fg: "#155e75" },
  succeeded: { bg: "#dcfce7", border: "#22c55e", fg: "#14532d" },
  failed: { bg: "#fee2e2", border: "#ef4444", fg: "#7f1d1d" },
  held: { bg: "#ffedd5", border: "#f97316", fg: "#7c2d12" },
};

const NODE_W = 190;
const NODE_H = 48;
const TERMINAL = new Set(["succeeded", "failed", "unsubmitted"]);

function layout(data: GraphData): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 30, ranksep: 60 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of data.nodes) {
    g.setNode(n.id, { width: NODE_W, height: NODE_H });
  }
  for (const e of data.edges) {
    g.setEdge(e.source, e.target);
  }
  dagre.layout(g);

  const nodes: Node[] = data.nodes.map((n) => {
    const pos = g.node(n.id);
    const s = STATUS_STYLE[n.status] ?? STATUS_STYLE.unsubmitted;
    return {
      id: n.id,
      position: { x: pos.x - NODE_W / 2, y: pos.y - NODE_H / 2 },
      data: { label: `${n.label}\n${n.id}` },
      style: {
        width: NODE_W,
        height: NODE_H,
        background: s.bg,
        border: `2px solid ${s.border}`,
        borderRadius: 8,
        color: s.fg,
        fontSize: 11,
        whiteSpace: "pre" as const,
        animation:
          n.status === "running" ? "pulse 2s ease-in-out infinite" : undefined,
      },
      // Tooltip: exec job id + status
      title: `${n.exec_job_id ?? n.id} — ${n.status}`,
    } as Node & { title: string };
  });

  const statusById = new Map(data.nodes.map((n) => [n.id, n.status]));
  const edges: Edge[] = data.edges.map((e, i) => {
    const tgt = statusById.get(e.target) ?? "unsubmitted";
    const done = statusById.get(e.source) === "succeeded";
    return {
      id: `e${i}`,
      source: e.source,
      target: e.target,
      animated: tgt === "running",
      style: {
        stroke: done ? "#334155" : "#cbd5e1",
        strokeWidth: done ? 2 : 1.5,
      },
    };
  });

  return { nodes, edges };
}

/** Interactive workflow DAG (React Flow + dagre): zoom/pan, status-colored
 *  nodes, live-polling while the run is active. */
export function DagViewer({ graphUrl, title, live = false, onClose }: Props) {
  const [data, setData] = useState<GraphData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchGraph = useCallback(() => {
    return fetch(graphUrl)
      .then(async (r) => {
        if (!r.ok) {
          const detail = await r
            .json()
            .then((d) => d.detail)
            .catch(() => r.statusText);
          throw new Error(detail || `HTTP ${r.status}`);
        }
        return r.json();
      })
      .then((d: GraphData) => {
        setData(d);
        return d;
      });
  }, [graphUrl]);

  useEffect(() => {
    let timer: number | undefined;
    let cancelled = false;

    const tick = () => {
      fetchGraph()
        .then((d) => {
          if (cancelled || !live) return;
          const active = d.nodes.some((n) => !TERMINAL.has(n.status));
          if (active || d.workflow_status === "running") {
            timer = window.setTimeout(tick, 4000);
          }
        })
        .catch((e) => !cancelled && setError(String(e.message || e)));
    };
    tick();

    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [fetchGraph, live]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const flow = useMemo(() => (data ? layout(data) : null), [data]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6"
      onClick={onClose}
    >
      <div
        className="flex h-[85vh] w-full max-w-6xl flex-col rounded-lg bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <h3 className="text-sm font-semibold text-fg">
            {title}
            {data?.workflow_status && (
              <span className="ml-2 text-xs font-normal text-fgsubtle">
                ({data.workflow_status})
              </span>
            )}
          </h3>
          <button
            onClick={onClose}
            className="rounded p-1 text-fgsubtle hover:bg-muted hover:text-fgmuted"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="relative flex-1">
          {error ? (
            <p className="p-6 text-sm text-red-600 dark:text-rose-400">
              Could not render DAG: {error}
            </p>
          ) : flow ? (
            <ReactFlow
              nodes={flow.nodes}
              edges={flow.edges}
              fitView
              minZoom={0.1}
              nodesDraggable={false}
              nodesConnectable={false}
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={16} />
              <Controls showInteractive={false} />
            </ReactFlow>
          ) : (
            <p className="p-6 text-sm text-fgsubtle">Loading DAG…</p>
          )}
        </div>

        {/* Legend */}
        <div className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-2 text-xs text-fgmuted">
          {Object.entries(STATUS_STYLE)
            .filter(([k]) => k !== "queued")
            .map(([k, s]) => (
              <span key={k} className="flex items-center gap-1">
                <span
                  className="inline-block h-3 w-3 rounded"
                  style={{ background: s.bg, border: `1.5px solid ${s.border}` }}
                />
                {k}
              </span>
            ))}
          {live && <span className="ml-auto text-fgsubtle">live · 4s</span>}
        </div>
      </div>
    </div>
  );
}
