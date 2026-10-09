"use client";

/**
 * Preview a workflow file: the one dialog both the Results tab and the graph's
 * job panel open, so a file looks the same wherever you click it.
 *
 * Renderers are a registry, tried in order, first match wins. Adding a viewer
 * for a new format (NetCDF headers, FASTA, ...) is one entry here; nothing that
 * opens a preview has to know it exists. The API still decides whether a file
 * is previewable at all (size, binary), so this only decides *how*.
 */

import { useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  FileArchive,
  FileImage,
  FileJson,
  FileSpreadsheet,
  FileText,
} from "lucide-react";
import type { OutputFile } from "@/lib/api/client";
import { cn } from "@/lib/utils";

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/**
 * A file-type icon. Lucide rather than emoji: emoji render differently on
 * every OS, sit off the text baseline and read as playful next to a results
 * table. JSON gets its own icon because it gets its own viewer.
 */
export function FileTypeIcon({
  name,
  kind,
  className,
}: {
  name: string;
  kind: OutputFile["kind"];
  className?: string;
}) {
  const [Icon, tint] = /\.(json|geojson)$/i.test(name)
    ? [FileJson, "text-amber-600 dark:text-amber-400"]
    : kind === "image"
      ? [FileImage, "text-violet-600 dark:text-violet-400"]
      : kind === "table"
        ? [FileSpreadsheet, "text-emerald-600 dark:text-emerald-400"]
        : kind === "text"
          ? [FileText, "text-pegasus-600 dark:text-pegasus-300"]
          : [FileArchive, "text-fgsubtle"];
  return <Icon aria-hidden="true" size={16} className={cn("shrink-0", tint, className)} />;
}

/** What a preview needs, wherever the file came from. */
export interface PreviewTarget {
  name: string;
  /** Shown in the header; the path a user would recognise. */
  path: string;
  kind: OutputFile["kind"];
  url: string;
  downloadUrl: string;
}

interface Renderer {
  matches: (t: PreviewTarget) => boolean;
  /** Images render from the URL; everything else is read as text first. */
  needsText: boolean;
  render: (t: PreviewTarget, text: string) => ReactNode;
}

const isJson = (t: PreviewTarget) => /\.(json|geojson)$/i.test(t.name);

const RENDERERS: Renderer[] = [
  {
    matches: (t) => t.kind === "image",
    needsText: false,
    render: (t) => (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={t.url}
        alt={t.name}
        className="mx-auto max-h-[70vh] max-w-full object-contain"
      />
    ),
  },
  // Before plain text: .json is classified as text server-side.
  { matches: isJson, needsText: true, render: (_t, text) => <JsonPreview text={text} /> },
  { matches: (t) => t.kind === "table", needsText: true, render: (_t, text) => <TablePreview text={text} /> },
  { matches: (t) => t.kind === "text", needsText: true, render: (_t, text) => <TextPreview text={text} /> },
];

export function FilePreviewDialog({
  target,
  onClose,
}: {
  target: PreviewTarget;
  onClose: () => void;
}) {
  const renderer = RENDERERS.find((r) => r.matches(target));
  const { data: text, isLoading, error } = useQuery({
    queryKey: ["file-preview", target.url],
    enabled: !!renderer?.needsText,
    queryFn: async () => {
      const res = await fetch(target.url);
      if (!res.ok) throw new Error(`Couldn't read the file (${res.status})`);
      return res.text();
    },
  });

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-6"
      role="dialog"
      aria-modal="true"
      aria-label={`Preview of ${target.name}`}
      onClick={onClose}
    >
      <div
        className="flex max-h-full w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-line bg-surface"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <span className="min-w-0 truncate text-sm font-medium text-fg">
            {target.path}
          </span>
          <span className="flex shrink-0 items-center gap-2">
            <a
              href={target.downloadUrl}
              download={target.name}
              className="rounded border border-line px-2 py-0.5 text-xs text-fgmuted hover:text-fg"
            >
              Download
            </a>
            <button
              onClick={onClose}
              aria-label="Close preview"
              className="rounded px-2 py-0.5 text-sm text-fgmuted hover:bg-base hover:text-fg"
            >
              ✕
            </button>
          </span>
        </div>

        <div className="min-h-0 flex-1 overflow-auto p-4">
          {!renderer ? (
            <p className="text-sm text-fgmuted">
              No preview for this kind of file. Download it to open it.
            </p>
          ) : error ? (
            <p className="text-sm text-red-600 dark:text-rose-400">
              {(error as Error).message}
            </p>
          ) : renderer.needsText && isLoading ? (
            <p className="text-sm text-fgmuted">Loading…</p>
          ) : (
            renderer.render(target, text ?? "")
          )}
        </div>
      </div>
    </div>
  );
}

function TextPreview({ text }: { text: string }) {
  return (
    <pre className="whitespace-pre-wrap [overflow-wrap:anywhere] font-mono text-xs text-fg">
      {text}
    </pre>
  );
}

/**
 * First rows of a CSV/TSV as a table.
 *
 * Deliberately naive: split on the delimiter, no quoted-field handling. A
 * scientist glancing at a result file is served by seeing columns; anyone who
 * needs the parse to be exactly right needs the file itself, which is one
 * click away. Row cap keeps a million-line CSV from freezing the tab.
 */
function TablePreview({ text }: { text: string }) {
  const MAX_ROWS = 200;
  const lines = text.split(/\r?\n/).filter((l) => l.length > 0);
  if (lines.length === 0) return <p className="text-sm text-fgmuted">Empty file.</p>;

  const delimiter = lines[0].includes("\t") ? "\t" : ",";
  const rows = lines.slice(0, MAX_ROWS).map((l) => l.split(delimiter));
  const [header, ...body] = rows;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="border-b border-line">
            {header.map((cell, i) => (
              <th key={i} className="whitespace-nowrap px-2 py-1 font-medium text-fgmuted">
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {body.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j} className={cn("whitespace-nowrap px-2 py-1 text-fg")}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {lines.length > MAX_ROWS && (
        <p className="mt-2 text-xs text-fgsubtle">
          Showing the first {MAX_ROWS} of {lines.length} rows — download the file
          for the rest.
        </p>
      )}
    </div>
  );
}

/* ─── JSON ─── */

/**
 * A JSON file as a collapsible tree, with the raw text one click away.
 *
 * Workflow outputs are often JSON (earthquake writes five), and as a wall of
 * text the structure is the first thing lost. Opens two levels deep: enough to
 * see the shape without unfolding a 10,000-element array on arrival.
 */
function JsonPreview({ text }: { text: string }) {
  const parsed = useMemo(() => {
    try {
      return { ok: true as const, value: JSON.parse(text) as unknown };
    } catch (e) {
      return { ok: false as const, message: (e as Error).message };
    }
  }, [text]);
  const [raw, setRaw] = useState(false);
  // Bumping the key remounts the tree with a new default depth: the simplest
  // way to make "Expand all" override nodes the user toggled by hand.
  const [depth, setDepth] = useState({ value: 2, key: 0 });

  if (!parsed.ok) {
    return (
      <div className="space-y-2">
        <p className="text-xs text-amber-700 dark:text-amber-400">
          Not valid JSON ({parsed.message}); showing the text.
        </p>
        <TextPreview text={text} />
      </div>
    );
  }

  const button =
    "rounded border border-line px-2 py-0.5 text-xs text-fgmuted hover:border-pegasus-400 hover:text-pegasus-700 dark:hover:text-pegasus-300";
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {!raw && (
          <>
            <button
              className={button}
              onClick={() => setDepth((d) => ({ value: Infinity, key: d.key + 1 }))}
            >
              Expand all
            </button>
            <button
              className={button}
              onClick={() => setDepth((d) => ({ value: 1, key: d.key + 1 }))}
            >
              Collapse all
            </button>
          </>
        )}
        <button className={cn(button, "ml-auto")} onClick={() => setRaw((r) => !r)}>
          {raw ? "Tree" : "Raw"}
        </button>
      </div>
      {raw ? (
        <TextPreview text={JSON.stringify(parsed.value, null, 2)} />
      ) : (
        <div key={depth.key} className="font-mono text-xs leading-relaxed">
          <JsonNode value={parsed.value} depth={0} openDepth={depth.value} />
        </div>
      )}
    </div>
  );
}

/** Children shown before a "show more" button, so a huge array stays usable. */
const JSON_PAGE = 100;

function JsonScalar({ value }: { value: unknown }) {
  if (value === null) return <span className="text-fgsubtle">null</span>;
  if (typeof value === "string")
    return (
      <span className="[overflow-wrap:anywhere] text-emerald-700 dark:text-emerald-400">
        &quot;{value}&quot;
      </span>
    );
  if (typeof value === "number")
    return <span className="text-pegasus-700 dark:text-pegasus-300">{String(value)}</span>;
  if (typeof value === "boolean")
    return <span className="text-violet-700 dark:text-violet-300">{String(value)}</span>;
  return <span>{String(value)}</span>;
}

function JsonNode({
  value,
  depth,
  openDepth,
  name,
}: {
  value: unknown;
  depth: number;
  openDepth: number;
  name?: string;
}) {
  const isArray = Array.isArray(value);
  const isObject = value !== null && typeof value === "object";
  const [open, setOpen] = useState(depth < openDepth);
  const [shown, setShown] = useState(JSON_PAGE);

  const label =
    name !== undefined ? (
      <span className="text-fg">
        {name}
        <span className="text-fgsubtle">: </span>
      </span>
    ) : null;

  if (!isObject) {
    return (
      <div style={{ paddingLeft: depth ? 16 : 0 }}>
        {label}
        <JsonScalar value={value} />
      </div>
    );
  }

  const entries: [string, unknown][] = isArray
    ? (value as unknown[]).map((v, i) => [String(i), v])
    : Object.entries(value as Record<string, unknown>);
  const brackets = isArray ? ["[", "]"] : ["{", "}"];
  const summary = isArray ? `${entries.length} items` : `${entries.length} keys`;

  return (
    <div style={{ paddingLeft: depth ? 16 : 0 }}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="rounded text-left hover:bg-base"
      >
        <span className="inline-block w-3 text-fgsubtle">{open ? "▾" : "▸"}</span>
        {label}
        <span className="text-fgsubtle">
          {brackets[0]}
          {!open && ` ${summary} ${brackets[1]}`}
        </span>
      </button>
      {open && (
        <>
          {entries.slice(0, shown).map(([k, v]) => (
            <JsonNode
              key={k}
              name={isArray ? undefined : k}
              value={v}
              depth={depth + 1}
              openDepth={openDepth}
            />
          ))}
          {entries.length > shown && (
            <button
              onClick={() => setShown((n) => n + JSON_PAGE)}
              className="ml-4 text-pegasus-700 hover:underline dark:text-pegasus-300"
            >
              … show {Math.min(JSON_PAGE, entries.length - shown)} more of{" "}
              {entries.length - shown}
            </button>
          )}
          <div className="text-fgsubtle">{brackets[1]}</div>
        </>
      )}
    </div>
  );
}
