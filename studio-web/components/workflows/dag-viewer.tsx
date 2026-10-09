"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import { Download } from "lucide-react";
import dagre from "@dagrejs/dagre";
import {
  JobFilesPanel,
  type JobFilesSource,
} from "@/components/workflows/job-files-panel";
import "@xyflow/react/dist/style.css";

interface GraphNode {
  id: string;
  label: string;
  transformation?: string | null;
  status: string;
  exec_job_id?: string;
  pegasus?: boolean; // Pegasus auxiliary job (stage-in/out, create-dir, cleanup…)
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
  /** Render in place instead of as a modal over the page (#314). The graph is
   *  the thing you look at while working, so on the analyses page it fills a
   *  pane rather than making you open and dismiss a dialog to see it. */
  inline?: boolean;
  /** Extra controls for the header — the run picker, when inline. */
  headerExtra?: React.ReactNode;
  onClose?: () => void;
  /** Click a job to see the files it reads and writes. Omit to disable. */
  jobFiles?: JobFilesSource;
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

// Field separator for the composite map keys below. It has to be a character
// no job id or transformation name can contain, and it is written as an escape
// rather than typed literally: a raw NUL byte in the source makes git classify
// the whole file as binary, so every diff of it renders as
// "Bin 21074 -> 31775 bytes" and grep stops finding anything in it.
const SEP = "\u0000";

const NODE_W = 190;
const NODE_H = 48;
const GROUP_H = 62;
// Below this, collapsing costs more than it saves: you lose the individual
// ids and gain almost no width back.
const MIN_GROUP = 4;
// Statuses that mean the run is still making progress. "unsubmitted" is
// deliberately not in this set NOR treated as terminal on its own: right
// after Submit every node is unsubmitted, so classing it terminal froze the
// DAG at its first fetch — a run counts as active while any node is
// in-flight OR nothing has started yet (all unsubmitted).
const ACTIVE = new Set(["pending", "queued", "running", "held"]);

// Which status a group wears when its members disagree. Ordered by what the
// user needs to see: 140 succeeded and 7 failed is a failed group, because the
// seven are the reason you are looking at it.
const STATUS_PRECEDENCE = [
  "failed",
  "held",
  "running",
  "queued",
  "pending",
  "unsubmitted",
  "succeeded",
];

// Plain words for the breakdown line under a group's name.
const STATUS_WORD: Record<string, string> = {
  succeeded: "done",
  running: "running",
  failed: "failed",
  held: "held",
  pending: "pending",
  queued: "queued",
  unsubmitted: "not started",
};

/** One drawn node: either a single job, or a collapsed fan-out of them. */
interface DisplayNode {
  id: string;
  label: string;
  status: string;
  members: GraphNode[];
  statusCounts: Map<string, number>;
  pegasus: boolean;
}

/**
 * Collapse structurally interchangeable siblings into one node (#314).
 *
 * A Pegasus scatter is the normal case, not an edge case: 1000Genomes puts 167
 * `mutation_overlap` jobs on a single dagre rank. At NODE_W 190 + nodesep 30
 * that rank is ~36,700px wide, so fitting it in a ~1,200px pane needs zoom
 * 0.033 — below `minZoom`, which means `fitView` cannot fit and the graph
 * silently overflows at roughly 19x5px per node. Lowering `minZoom` does not
 * help: a 190px node at zoom 0.033 is 6px and legible to nobody. The graph is
 * too wide because we draw 167 identical things, so that is what changes.
 *
 * Nodes group when they share a transformation and the same set of parent
 * groups. Children are deliberately *not* part of the key: in a chained
 * fan-out (map then reduce-per-item) every sibling has a different child, so
 * requiring identical children would never collapse anything. Ignoring them
 * and iterating to a fixpoint lets a whole chain of fan-outs collapse rank by
 * rank.
 *
 * The trade is that a group-to-group edge is a summary — it says "these feed
 * those", not which fed which. That is what a summary view is for, and
 * "Show every task" turns it off.
 */
function clusterGraph(data: GraphData): {
  nodes: DisplayNode[];
  edges: { source: string; target: string }[];
} {
  const parents = new Map<string, string[]>();
  for (const n of data.nodes) parents.set(n.id, []);
  for (const e of data.edges) {
    // Graph data can reference an id that isn't in `nodes` (a filtered-out
    // Pegasus job, say) — skip rather than inventing a node for it.
    if (!parents.has(e.target) || !parents.has(e.source)) continue;
    parents.get(e.target)!.push(e.source);
  }

  let assign = new Map(data.nodes.map((n) => [n.id, n.id]));
  // Six passes is far more than any real DAG needs; the loop exits as soon as
  // an iteration changes nothing.
  for (let pass = 0; pass < 6; pass++) {
    const buckets = new Map<string, string[]>();
    for (const n of data.nodes) {
      const family = n.transformation || n.label;
      const ps = Array.from(
        new Set((parents.get(n.id) ?? []).map((p) => assign.get(p) ?? p))
      ).sort();
      const key = `${n.pegasus ? `aux${SEP}` : ""}${family}${SEP}${ps.join(",")}`;
      const bucket = buckets.get(key);
      if (bucket) bucket.push(n.id);
      else buckets.set(key, [n.id]);
    }

    const next = new Map<string, string>();
    let changed = false;
    for (const ids of buckets.values()) {
      // The lowest member id keys the group, so the id is stable across polls
      // and React Flow doesn't remount every node on each refresh.
      const cid =
        ids.length >= MIN_GROUP ? `grp:${[...ids].sort()[0]}` : undefined;
      for (const id of ids) {
        const to = cid ?? id;
        next.set(id, to);
        if ((assign.get(id) ?? id) !== to) changed = true;
      }
    }
    assign = next;
    if (!changed) break;
  }

  const grouped = new Map<string, GraphNode[]>();
  for (const n of data.nodes) {
    const cid = assign.get(n.id) ?? n.id;
    const g = grouped.get(cid);
    if (g) g.push(n);
    else grouped.set(cid, [n]);
  }

  const nodes: DisplayNode[] = [];
  for (const [id, members] of grouped) {
    const statusCounts = new Map<string, number>();
    for (const m of members) {
      statusCounts.set(m.status, (statusCounts.get(m.status) ?? 0) + 1);
    }
    const status =
      STATUS_PRECEDENCE.find((s) => statusCounts.has(s)) ??
      members[0]?.status ??
      "unsubmitted";
    nodes.push({
      id,
      label: members[0]?.transformation || members[0]?.label || id,
      status,
      members,
      statusCounts,
      pegasus: !!members[0]?.pegasus,
    });
  }

  const seen = new Set<string>();
  const edges: { source: string; target: string }[] = [];
  for (const e of data.edges) {
    const s = assign.get(e.source) ?? e.source;
    const t = assign.get(e.target) ?? e.target;
    // Edges inside a collapsed group have nowhere to go.
    if (s === t) continue;
    const key = `${s}${SEP}${t}`;
    if (seen.has(key)) continue;
    seen.add(key);
    edges.push({ source: s, target: t });
  }

  return { nodes, edges };
}

/** Every job as its own node — what `clusterGraph` returns with grouping off. */
function passthroughGraph(data: GraphData): {
  nodes: DisplayNode[];
  edges: { source: string; target: string }[];
} {
  return {
    nodes: data.nodes.map((n) => ({
      id: n.id,
      label: n.label,
      status: n.status,
      members: [n],
      statusCounts: new Map([[n.status, 1]]),
      pegasus: !!n.pegasus,
    })),
    edges: data.edges,
  };
}

/** "7 failed · 20 running · 140 done" — only the statuses actually present. */
function breakdown(counts: Map<string, number>): string {
  return STATUS_PRECEDENCE.filter((s) => counts.has(s))
    .map((s) => `${counts.get(s)} ${STATUS_WORD[s] ?? s}`)
    .join(" · ");
}

/** Geometry + paint for one node, kept separately so the picture can be drawn
 *  without reading it back out of the DOM (see `buildSvg`). */
interface PictureNode {
  x: number;
  y: number;
  w: number;
  h: number;
  bg: string;
  border: string;
  fg: string;
  dashed: boolean;
  strokeWidth: number;
  lines: string[];
  status: string;
}

interface PictureEdge {
  source: string;
  target: string;
  done: boolean;
}

/** Everything needed to draw the DAG as a standalone image. */
interface Picture {
  nodes: Map<string, PictureNode>;
  edges: PictureEdge[];
}

function layout(display: {
  nodes: DisplayNode[];
  edges: { source: string; target: string }[];
}): { nodes: Node[]; edges: Edge[]; picture: Picture } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 30, ranksep: 60 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of display.nodes) {
    g.setNode(n.id, {
      width: NODE_W,
      height: n.members.length > 1 ? GROUP_H : NODE_H,
    });
  }
  for (const e of display.edges) {
    g.setEdge(e.source, e.target);
  }
  dagre.layout(g);

  const picture: Picture = { nodes: new Map(), edges: [] };

  const nodes: Node[] = display.nodes.map((n) => {
    const h = n.members.length > 1 ? GROUP_H : NODE_H;
    // dagre returns undefined for an id it never laid out (malformed graph
    // data) — fall back to origin rather than crashing the whole modal.
    const pos = g.node(n.id) ?? { x: NODE_W / 2, y: h / 2 };
    const s = STATUS_STYLE[n.status] ?? STATUS_STYLE.unsubmitted;
    const aux = n.pegasus;
    const count = n.members.length;
    const single = n.members.length === 1 ? n.members[0] : undefined;

    const label =
      count > 1
        ? `${aux ? "⚙ " : ""}${n.label} ×${count}\n${breakdown(n.statusCounts)}`
        : `${aux ? "⚙ " : ""}${single?.label ?? n.label}\n${single?.id ?? n.id}`;

    const title =
      count > 1
        ? `${n.label} — ${count} tasks · ${breakdown(n.statusCounts)}`
        : `${single?.exec_job_id ?? n.id} — ${n.status}${aux ? " (Pegasus)" : ""}`;

    const x = pos.x - NODE_W / 2;
    const y = pos.y - h / 2;
    picture.nodes.set(n.id, {
      x,
      y,
      w: NODE_W,
      h,
      bg: s.bg,
      border: s.border,
      fg: s.fg,
      dashed: aux,
      strokeWidth: count > 1 ? 3 : 2,
      lines: label.split("\n"),
      status: n.status,
    });

    return {
      id: n.id,
      position: { x, y },
      data: { label },
      style: {
        width: NODE_W,
        height: h,
        background: s.bg,
        // Pegasus auxiliary jobs: dashed border + slightly muted to set them
        // apart from the user's compute jobs. A collapsed group gets a heavier
        // border so it reads as "many" before you read the ×N.
        border: `${count > 1 ? 3 : 2}px ${aux ? "dashed" : "solid"} ${s.border}`,
        borderRadius: 8,
        color: s.fg,
        fontSize: 11,
        opacity: aux ? 0.8 : 1,
        whiteSpace: "pre" as const,
        animation:
          n.status === "running" ? "pulse 2s ease-in-out infinite" : undefined,
      },
      title,
    } as Node & { title: string };
  });

  const statusById = new Map(display.nodes.map((n) => [n.id, n.status]));
  const edges: Edge[] = display.edges.map((e, i) => {
    const tgt = statusById.get(e.target) ?? "unsubmitted";
    const done = statusById.get(e.source) === "succeeded";
    picture.edges.push({ source: e.source, target: e.target, done });
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

  return { nodes, edges, picture };
}

// ---------------------------------------------------------------------------
// Saving the picture
//
// The image is drawn from `Picture` — the same geometry React Flow was handed —
// rather than screenshotting the pane. Two reasons: what you want to keep is the
// whole DAG, not the part that happened to be framed and at whatever zoom the
// viewport was left at; and rasterising live DOM needs a helper library plus
// every Tailwind rule inlined, which is a lot of machinery to reproduce nine
// rectangles and some curves.
// ---------------------------------------------------------------------------

const PIC_PAD = 24;
const PIC_TITLE_H = 34;
const PIC_LEGEND_H = 26;
const PIC_FONT =
  "ui-sans-serif, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";
// Browsers refuse canvases past ~16k on a side (and much less on iOS), so a
// 36,000px "Show every task" graph has to give up resolution rather than
// silently rasterise to nothing.
const PIC_MAX_PX = 8192;

function escapeXml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** A light background always: these get pasted into papers and slides, where a
 *  dark-mode screenshot is the wrong thing to have saved. */
function buildSvg(
  picture: Picture,
  title: string
): { svg: string; width: number; height: number } {
  const nodes = Array.from(picture.nodes.values());
  let minX = 0;
  let minY = 0;
  let maxX = NODE_W;
  let maxY = NODE_H;
  if (nodes.length > 0) {
    minX = Math.min(...nodes.map((n) => n.x));
    minY = Math.min(...nodes.map((n) => n.y));
    maxX = Math.max(...nodes.map((n) => n.x + n.w));
    maxY = Math.max(...nodes.map((n) => n.y + n.h));
  }

  const graphW = maxX - minX;
  const graphH = maxY - minY;
  const width = Math.round(Math.max(graphW + PIC_PAD * 2, 420));
  const height = Math.round(
    PIC_TITLE_H + graphH + PIC_PAD * 2 + PIC_LEGEND_H
  );
  const dx = PIC_PAD - minX;
  const dy = PIC_TITLE_H + PIC_PAD - minY;

  const parts: string[] = [];
  parts.push(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`,
    `<rect width="${width}" height="${height}" fill="#ffffff"/>`,
    `<text x="${PIC_PAD}" y="22" font-family="${PIC_FONT}" font-size="14" font-weight="600" fill="#111827">${escapeXml(
      title
    )}</text>`,
    `<g transform="translate(${dx},${dy})">`
  );

  for (const e of picture.edges) {
    const s = picture.nodes.get(e.source);
    const t = picture.nodes.get(e.target);
    if (!s || !t) continue;
    const sx = s.x + s.w / 2;
    const sy = s.y + s.h;
    const tx = t.x + t.w / 2;
    const ty = t.y;
    // Mirrors React Flow's default bottom-to-top bezier closely enough that the
    // saved picture reads as the same drawing.
    const c = Math.max(Math.abs(ty - sy) / 2, 20);
    parts.push(
      `<path d="M${sx.toFixed(1)},${sy.toFixed(1)} C${sx.toFixed(1)},${(
        sy + c
      ).toFixed(1)} ${tx.toFixed(1)},${(ty - c).toFixed(1)} ${tx.toFixed(
        1
      )},${ty.toFixed(1)}" fill="none" stroke="${
        e.done ? "#334155" : "#cbd5e1"
      }" stroke-width="${e.done ? 2 : 1.5}"/>`
    );
  }

  for (const n of nodes) {
    parts.push(
      `<rect x="${n.x.toFixed(1)}" y="${n.y.toFixed(1)}" width="${n.w}" height="${
        n.h
      }" rx="8" fill="${n.bg}" stroke="${n.border}" stroke-width="${
        n.strokeWidth
      }"${n.dashed ? ' stroke-dasharray="5 4"' : ""}/>`
    );
    const lh = 13;
    const cx = n.x + n.w / 2;
    const first = n.y + n.h / 2 - ((n.lines.length - 1) * lh) / 2 + 4;
    n.lines.forEach((line, i) => {
      parts.push(
        `<text x="${cx.toFixed(1)}" y="${(first + i * lh).toFixed(
          1
        )}" text-anchor="middle" font-family="${PIC_FONT}" font-size="11" fill="${
          n.fg
        }">${escapeXml(line)}</text>`
      );
    });
  }

  parts.push(`</g>`);

  // Legend: only the statuses this graph actually contains, so a finished run
  // does not ship a swatch for "held".
  const present = STATUS_PRECEDENCE.filter(
    (s) => s !== "queued" && nodes.some((n) => n.status === s)
  );
  let lx = PIC_PAD;
  const ly = height - PIC_PAD / 2 - 6;
  for (const s of present) {
    const style = STATUS_STYLE[s] ?? STATUS_STYLE.unsubmitted;
    parts.push(
      `<rect x="${lx}" y="${ly - 9}" width="10" height="10" rx="2" fill="${
        style.bg
      }" stroke="${style.border}" stroke-width="1.5"/>`,
      `<text x="${lx + 15}" y="${ly}" font-family="${PIC_FONT}" font-size="11" fill="#4b5563">${s}</text>`
    );
    lx += 15 + s.length * 6.2 + 14;
  }

  parts.push(`</svg>`);
  return { svg: parts.join(""), width, height };
}

async function svgToPngBlob(
  svg: string,
  width: number,
  height: number
): Promise<Blob> {
  const scale = Math.min(2, PIC_MAX_PX / Math.max(width, height, 1));
  const url = URL.createObjectURL(
    new Blob([svg], { type: "image/svg+xml;charset=utf-8" })
  );
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error("the browser could not read the SVG"));
      el.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("no 2d canvas context");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    ctx.drawImage(img, 0, 0);
    return await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (b) => (b ? resolve(b) : reject(new Error("the canvas came back empty"))),
        "image/png"
      )
    );
  } finally {
    URL.revokeObjectURL(url);
  }
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoking synchronously cancels the download in Safari; a delayed revoke
  // still frees the blob.
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function slugify(title: string): string {
  const s = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return s || "workflow-dag";
}

/* ─── Full-screen dialog size ─── */

// The dialog opened at a fixed 85vh × 72rem, which suited neither a laptop nor
// a big monitor, and a wide DAG is exactly when you want more room. Its size
// is now draggable from the corner and remembered per browser, like the
// inline pane's height.
const MODAL_SIZE_KEY = "dag-modal-size";
const MODAL_MIN_W = 480;
const MODAL_MIN_H = 320;
const MODAL_KEY_STEP = 40;

type ModalSize = { w: number; h: number };

// Storage can throw (private mode, blocked site data); a size we cannot read
// or keep just means the default size, never a broken dialog.
function readModalSize(): ModalSize | null {
  try {
    const v = JSON.parse(window.localStorage.getItem(MODAL_SIZE_KEY) ?? "null");
    if (v && typeof v.w === "number" && typeof v.h === "number") return v;
  } catch {
    /* default size */
  }
  return null;
}

function writeModalSize(size: ModalSize | null) {
  try {
    if (size) window.localStorage.setItem(MODAL_SIZE_KEY, JSON.stringify(size));
    else window.localStorage.removeItem(MODAL_SIZE_KEY);
  } catch {
    /* not remembered, still applied */
  }
}

// Only a floor here: the ceiling is `max-width/max-height: 100%` on the dialog,
// so a size saved on a big monitor still fits when the window shrinks.
function clampModalSize(w: number, h: number): ModalSize {
  return {
    w: Math.round(Math.max(MODAL_MIN_W, w)),
    h: Math.round(Math.max(MODAL_MIN_H, h)),
  };
}

/** Interactive workflow DAG (React Flow + dagre): zoom/pan, status-colored
 *  nodes, live-polling while the run is active. */
export function DagViewer({
  graphUrl,
  title,
  live = false,
  inline = false,
  headerExtra,
  onClose,
  jobFiles,
}: Props) {
  const [data, setData] = useState<GraphData | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Pegasus auxiliary jobs (stage-in/out, create-dir, cleanup, register) only
  // exist in a run's executable DAG, so the toggle is offered for run graphs.
  const [showPegasus, setShowPegasus] = useState(false);
  // Grouping on by default: the unclustered view is unreadable for any real
  // scatter, so it is the escape hatch rather than the starting point.
  const [groupFanout, setGroupFanout] = useState(true);
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const saveRef = useRef<HTMLDivElement | null>(null);

  const effectiveUrl = useMemo(() => {
    if (!showPegasus) return graphUrl;
    return graphUrl + (graphUrl.includes("?") ? "&" : "?") + "include_pegasus=true";
  }, [graphUrl, showPegasus]);

  const fetchGraph = useCallback(() => {
    return fetch(effectiveUrl)
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
        setError(null);
        return d;
      });
  }, [effectiveUrl]);

  useEffect(() => {
    let timer: number | undefined;
    let cancelled = false;

    const tick = () => {
      fetchGraph()
        .then((d) => {
          if (cancelled || !live) return;
          const anyActive = d.nodes.some((n) => ACTIVE.has(n.status));
          const allUnsubmitted =
            d.nodes.length > 0 &&
            d.nodes.every((n) => n.status === "unsubmitted");
          if (anyActive || allUnsubmitted || d.workflow_status === "running") {
            timer = window.setTimeout(tick, 4000);
          }
        })
        .catch((e) => {
          if (cancelled) return;
          setError(String(e.message || e));
          // One failed 4s poll must not kill a working live view: keep the
          // last good graph rendered (see render order below) and keep
          // polling — the next success clears the error.
          if (live) timer = window.setTimeout(tick, 4000);
        });
    };
    tick();

    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [fetchGraph, live]);

  const shellRef = useRef<HTMLDivElement>(null);
  const [modalSize, setModalSize] = useState<ModalSize | null>(null);
  useEffect(() => {
    if (!inline) setModalSize(readModalSize());
  }, [inline]);

  // The dialog is centred, so growing it by d moves each edge by d/2. Doubling
  // the pointer delta keeps the grip under the cursor instead of lagging it.
  const startResize = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const box = shellRef.current?.getBoundingClientRect();
    if (!box) return;
    const sx = e.clientX;
    const sy = e.clientY;
    let last: ModalSize = { w: box.width, h: box.height };
    const move = (ev: PointerEvent) => {
      last = clampModalSize(
        box.width + 2 * (ev.clientX - sx),
        box.height + 2 * (ev.clientY - sy)
      );
      setModalSize(last);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      writeModalSize(last);
    };
    document.body.style.cursor = "nwse-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }, []);

  const resetSize = useCallback(() => {
    setModalSize(null);
    writeModalSize(null);
  }, []);

  const onGripKeyDown = useCallback((e: React.KeyboardEvent) => {
    const box = shellRef.current?.getBoundingClientRect();
    if (!box) return;
    const step = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowDown: [0, 1], ArrowUp: [0, -1] }[
      e.key as "ArrowRight"
    ];
    if (e.key === "Home") {
      e.preventDefault();
      resetSize();
      return;
    }
    if (!step) return;
    e.preventDefault();
    const next = clampModalSize(
      box.width + step[0] * MODAL_KEY_STEP,
      box.height + step[1] * MODAL_KEY_STEP
    );
    setModalSize(next);
    writeModalSize(next);
  }, [resetSize]);

  // Close on a backdrop click only when the press also started on the
  // backdrop: a resize that ends with the pointer outside the dialog produces
  // a click on the backdrop too, and that must not dismiss it.
  const pressedBackdrop = useRef(false);

  useEffect(() => {
    // Escape dismisses a dialog. Inline there is nothing to dismiss, and
    // stealing the key from the page would be a surprise.
    if (inline || !onClose) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, inline]);

  useEffect(() => {
    if (!saveOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!saveRef.current?.contains(e.target as globalThis.Node)) {
        setSaveOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [saveOpen]);

  const display = useMemo(
    () => (data ? (groupFanout ? clusterGraph(data) : passthroughGraph(data)) : null),
    [data, groupFanout]
  );

  // The node whose files are open. Kept as an id and resolved against the
  // current picture, so a live re-poll updates its statuses in place and a
  // regrouping that removes it simply closes the panel.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = useMemo(
    () => (selectedId ? display?.nodes.find((n) => n.id === selectedId) : undefined),
    [display, selectedId]
  );
  const selectedJobs = useMemo(
    () =>
      (selected?.members ?? []).map((m) => ({
        id: m.id,
        label: m.label,
        status: m.status,
      })),
    [selected]
  );

  const flow = useMemo<
    | { nodes: Node[]; edges: Edge[]; picture: Picture }
    | { layoutError: string }
    | null
  >(() => {
    if (!display) return null;
    try {
      return layout(display);
    } catch (e) {
      // A graph dagre can't lay out must degrade to an error message, not a
      // render-time crash that white-screens the modal.
      return { layoutError: String(e instanceof Error ? e.message : e) };
    }
  }, [display]);

  const picture = flow && !("layoutError" in flow) ? flow.picture : null;

  const savePicture = useCallback(
    async (format: "png" | "svg") => {
      setSaveOpen(false);
      if (!picture) return;
      try {
        setSaveError(null);
        const { svg, width, height } = buildSvg(picture, title);
        const name = slugify(title);
        if (format === "svg") {
          downloadBlob(
            new Blob([svg], { type: "image/svg+xml;charset=utf-8" }),
            `${name}.svg`
          );
        } else {
          downloadBlob(await svgToPngBlob(svg, width, height), `${name}.png`);
        }
      } catch (e) {
        setSaveError(String(e instanceof Error ? e.message : e));
      }
    },
    [picture, title]
  );

  const taskCount = data?.nodes.length ?? 0;
  const drawnCount = display?.nodes.length ?? 0;
  // Only worth offering the toggle when grouping is actually doing something.
  const collapses = taskCount > drawnCount || (!groupFanout && taskCount > 0);
  // With grouping off on a big graph, fitView still cannot reach a readable
  // zoom. Say so rather than letting the user conclude the DAG is broken.
  const tooWide = drawnCount > 120;

  const shell = (
      <div
        ref={shellRef}
        className={
          inline
            ? "flex h-full min-h-0 w-full flex-col bg-surface"
            : modalSize
              ? "relative flex max-h-full max-w-full flex-col rounded-lg bg-surface shadow-xl"
              : "relative flex h-[85vh] max-h-full w-full max-w-6xl flex-col rounded-lg bg-surface shadow-xl"
        }
        style={
          !inline && modalSize
            ? { width: modalSize.w, height: modalSize.h }
            : undefined
        }
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
            {taskCount > 0 && (
              <span className="ml-2 text-xs font-normal text-fgsubtle">
                {taskCount} task{taskCount !== 1 ? "s" : ""}
                {drawnCount < taskCount && ` in ${drawnCount} groups`}
              </span>
            )}
          </h3>
          <div className="flex items-center gap-3">
            {headerExtra}
            {collapses && (
              <label
                className="flex cursor-pointer select-none items-center gap-1.5 text-xs text-fgmuted"
                title="Draw every task separately. A large scatter will not fit on screen."
              >
                <input
                  type="checkbox"
                  checked={!groupFanout}
                  onChange={(e) => setGroupFanout(!e.target.checked)}
                  className="h-3.5 w-3.5 accent-pegasus-600"
                />
                Show every task
              </label>
            )}
            {live && (
              <label
                className="flex cursor-pointer select-none items-center gap-1.5 text-xs text-fgmuted"
                title="Show Pegasus auxiliary jobs (stage-in/out, create-dir, cleanup, register)"
              >
                <input
                  type="checkbox"
                  checked={showPegasus}
                  onChange={(e) => setShowPegasus(e.target.checked)}
                  className="h-3.5 w-3.5 accent-pegasus-600"
                />
                Show Pegasus jobs
              </label>
            )}
            <div className="relative" ref={saveRef}>
              <button
                onClick={() => setSaveOpen((v) => !v)}
                disabled={!picture}
                className="flex items-center gap-1 rounded border border-line px-2 py-1 text-xs text-fgmuted hover:bg-muted hover:text-fg disabled:cursor-not-allowed disabled:opacity-50"
                title="Save the whole graph as an image — not just the part on screen"
                aria-haspopup="menu"
                aria-expanded={saveOpen}
              >
                <Download className="h-3.5 w-3.5" />
                Save image
              </button>
              {saveOpen && (
                <div
                  role="menu"
                  className="absolute right-0 top-full z-30 mt-1 min-w-[9rem] overflow-hidden rounded border border-line bg-surface py-1 shadow-lg"
                >
                  <button
                    role="menuitem"
                    onClick={() => savePicture("png")}
                    className="block w-full px-3 py-1.5 text-left text-xs text-fgmuted hover:bg-muted hover:text-fg"
                  >
                    PNG
                  </button>
                  <button
                    role="menuitem"
                    onClick={() => savePicture("svg")}
                    className="block w-full px-3 py-1.5 text-left text-xs text-fgmuted hover:bg-muted hover:text-fg"
                    title="Vector — scales without blurring, for papers and slides"
                  >
                    SVG
                  </button>
                </div>
              )}
            </div>
            {/* Nothing to dismiss when the graph is the pane. */}
            {!inline && onClose && (
              <button
                onClick={onClose}
                className="rounded p-1 text-fgsubtle hover:bg-muted hover:text-fgmuted"
                aria-label="Close"
              >
                ✕
              </button>
            )}
          </div>
        </div>

        <div className="relative min-h-0 flex-1">
          {/* Render order matters: a fetched graph wins over a transient
              poll error, so a single failed refresh shows the last good DAG
              (with a retry note in the footer) instead of replacing it. */}
          {flow && "layoutError" in flow ? (
            <p className="p-6 text-sm text-red-600 dark:text-rose-400">
              Could not render DAG: {flow.layoutError}
            </p>
          ) : error && !flow ? (
            <p className="p-6 text-sm text-red-600 dark:text-rose-400">
              Could not render DAG: {error}
            </p>
          ) : flow ? (
            <>
              {tooWide && (
                <div className="pointer-events-none absolute inset-x-0 top-2 z-10 flex justify-center">
                  <p className="pointer-events-auto rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-xs text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-300">
                    {drawnCount} nodes on screen — too many to read at once.{" "}
                    {!groupFanout && (
                      <button
                        onClick={() => setGroupFanout(true)}
                        className="font-semibold underline"
                      >
                        Group repeated steps
                      </button>
                    )}
                  </p>
                </div>
              )}
              <ReactFlow
                className={jobFiles ? "[&_.react-flow__node]:cursor-pointer" : undefined}
                nodes={flow.nodes}
                edges={flow.edges}
                fitView
                // 0.04 rather than 0.1 so "Show every task" can at least frame
                // a large graph. It will be illegible at that zoom — that is
                // what the notice above is for — but a framed illegible graph
                // beats one that silently runs off both edges.
                minZoom={0.04}
                nodesDraggable={false}
                nodesConnectable={false}
                // Inline, the graph sits inside the page's scroller, and React
                // Flow's default is to `preventDefault()` every wheel event
                // over its pane and zoom instead. That makes it a scroll trap:
                // once the graph is under the pointer the page stops scrolling
                // and the wheel silently zooms a graph you were only passing
                // over. Zoom stays available on ctrl/⌘+wheel, trackpad pinch
                // and the +/- controls. In the modal the graph *is* the
                // surface, so wheel-zoom is what you want there.
                preventScrolling={!inline}
                proOptions={{ hideAttribution: true }}
                onNodeClick={jobFiles ? (_e, node) => setSelectedId(node.id) : undefined}
                onPaneClick={jobFiles ? () => setSelectedId(null) : undefined}
              >
                <Background gap={16} />
                <Controls showInteractive={false} />
              </ReactFlow>
              {jobFiles && selected && (
                <JobFilesPanel
                  title={selected.label}
                  jobs={selectedJobs}
                  source={jobFiles}
                  onClose={() => setSelectedId(null)}
                />
              )}
            </>
          ) : (
            <p className="p-6 text-sm text-fgsubtle">Loading DAG…</p>
          )}
        </div>

        {/* Legend */}
        <div
          className={`flex flex-wrap items-center gap-3 border-t border-line py-2 pl-4 text-xs text-fgmuted ${
            inline ? "pr-4" : "pr-7"
          }`}
        >
          {jobFiles && (
            <span className="font-medium text-fg">Click a step to see its files ·</span>
          )}
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
          {showPegasus && (
            <span className="flex items-center gap-1">
              <span
                className="inline-block h-3 w-3 rounded border-2 border-dashed border-fgsubtle opacity-70"
              />
              ⚙ Pegasus job
            </span>
          )}
          {saveError && (
            <span className="text-red-600 dark:text-rose-400">
              Could not save the image: {saveError}
            </span>
          )}
          {live && (
            <span className="ml-auto text-fgsubtle">
              {error && flow ? (
                <span className="text-amber-600 dark:text-amber-400">
                  refresh failed · retrying
                </span>
              ) : (
                "live · 4s"
              )}
            </span>
          )}
        </div>

        {!inline && (
          // Corner grip. A pointer handler rather than CSS `resize`, because
          // the native grip on a centred box lags the cursor by half.
          <div
            role="separator"
            aria-label="Resize the graph window"
            title="Drag to resize · double-click to reset"
            tabIndex={0}
            onPointerDown={startResize}
            onDoubleClick={resetSize}
            onKeyDown={onGripKeyDown}
            className="absolute bottom-0 right-0 z-10 flex h-5 w-5 cursor-nwse-resize items-end justify-end rounded-br-lg p-1 text-fgsubtle hover:text-pegasus-600 focus:text-pegasus-600 focus:outline-none"
          >
            <svg viewBox="0 0 10 10" className="h-2.5 w-2.5" aria-hidden="true">
              <path
                d="M9 1 1 9M9 5 5 9"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
          </div>
        )}
      </div>
  );

  if (inline) return shell;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6"
      onPointerDown={(e) => {
        pressedBackdrop.current = e.target === e.currentTarget;
      }}
      onClick={(e) => {
        if (pressedBackdrop.current && e.target === e.currentTarget) onClose?.();
      }}
    >
      {shell}
    </div>
  );
}
