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
  ArrowDown,
  ArrowUp,
  FileArchive,
  FileCode,
  FileImage,
  FileJson,
  FileSpreadsheet,
  FileText,
  FileType2,
  Search,
} from "lucide-react";
import type { OutputFile } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import {
  compareCells,
  detectDelimiter,
  numericColumns,
  parseCsv,
} from "@/lib/preview/csv";

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
      : kind === "pdf"
        ? [FileType2, "text-rose-600 dark:text-rose-400"]
        : kind === "html"
          ? [FileCode, "text-orange-600 dark:text-orange-400"]
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
  /** Bytes, for the "showing the first part" note. */
  size?: number;
  /** Too big to show whole: fetch and show only its start (#460). */
  partial?: boolean;
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
  {
    // The browser's own viewer: paging, zoom, search, print, for nothing.
    matches: (t) => t.kind === "pdf",
    needsText: false,
    render: (t) => (
      <iframe
        src={t.url}
        title={t.name}
        className="h-[75vh] w-full rounded border border-line bg-white"
      />
    ),
  },
  // A whole HTML file renders sandboxed; one too big to render shows the
  // start of its source through the text renderer below.
  {
    matches: (t) => t.kind === "html" && !t.partial,
    needsText: false,
    render: (t) => <HtmlPreview target={t} />,
  },
  // Before plain text: .json is classified as text server-side. A partial
  // JSON file is cut mid-structure and cannot parse, so it shows as text.
  {
    matches: (t) => isJson(t) && !t.partial,
    needsText: true,
    render: (_t, text) => <JsonPreview text={text} />,
  },
  {
    matches: (t) => t.kind === "table",
    needsText: true,
    render: (t, text) => <TablePreview text={text} name={t.name} />,
  },
  {
    matches: (t) => t.kind === "text" || (t.kind === "html" && !!t.partial),
    needsText: true,
    render: (_t, text) => <TextPreview text={text} />,
  },
];

/** What the server sends for a partial preview (run_outputs.PREVIEW_MAX_BYTES). */
const PARTIAL_BYTES = 2 * 1024 * 1024;

export function FilePreviewDialog({
  target,
  onClose,
}: {
  target: PreviewTarget;
  onClose: () => void;
}) {
  const renderer = RENDERERS.find((r) => r.matches(target));
  const fetchUrl = target.partial
    ? `${target.url}${target.url.includes("?") ? "&" : "?"}head=1`
    : target.url;
  const { data: text, isLoading, error } = useQuery({
    queryKey: ["file-preview", fetchUrl],
    enabled: !!renderer?.needsText,
    queryFn: async () => {
      const res = await fetch(fetchUrl);
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

        {target.partial && (
          <p className="border-b border-line bg-amber-50 px-4 py-1.5 text-xs text-amber-800 dark:bg-amber-400/10 dark:text-amber-300">
            Showing the first {formatBytes(PARTIAL_BYTES)}
            {target.size ? ` of ${formatBytes(target.size)}` : ""}. Download the
            file for the rest.
          </p>
        )}
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

/**
 * An HTML output, rendered (#470).
 *
 * Untrusted, and served from Studio's own origin, so it is framed with
 * `sandbox="allow-scripts"` and deliberately *without* `allow-same-origin`:
 * scripts run — interactive reports need them — but in an opaque origin that
 * cannot read Studio's cookies or responses, submit forms, open popups or
 * navigate the page. The server adds the same sandbox as a CSP header, so the
 * file URL opened directly is contained too. Reports that load sibling files
 * (`report_files/…`) will not find them through the preview URL; a
 * self-contained report renders fully.
 */
function HtmlPreview({ target }: { target: PreviewTarget }) {
  const [source, setSource] = useState(false);
  const { data: text, isLoading } = useQuery({
    queryKey: ["file-preview-source", target.url],
    enabled: source,
    queryFn: async () => {
      const res = await fetch(target.url);
      if (!res.ok) throw new Error(`Couldn't read the file (${res.status})`);
      return res.text();
    },
  });
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-xs text-fgsubtle">
          Rendered in a sandbox: scripts run, but cannot reach Studio.
        </span>
        <button
          onClick={() => setSource((s) => !s)}
          className="ml-auto rounded border border-line px-2 py-0.5 text-xs text-fgmuted hover:border-pegasus-400 hover:text-pegasus-700 dark:hover:text-pegasus-300"
        >
          {source ? "Rendered" : "Source"}
        </button>
      </div>
      {source ? (
        isLoading ? (
          <p className="text-sm text-fgmuted">Loading…</p>
        ) : (
          <TextPreview text={text ?? ""} />
        )
      ) : (
        <iframe
          src={target.url}
          title={target.name}
          sandbox="allow-scripts"
          referrerPolicy="no-referrer"
          className="h-[70vh] w-full rounded border border-line bg-white"
        />
      )}
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

/** Rows drawn at once. Sorting and filtering see every parsed row; only the
 *  drawing is capped, which is what keeps a long table responsive. */
const TABLE_DRAW = 1000;

/**
 * A CSV/TSV as a sortable, filterable table (#460).
 *
 * Parsed properly (quoted fields, see lib/preview/csv) — the old preview split
 * on commas and shifted every column after the first quoted one. Click a
 * header to sort (numbers as numbers), type to filter rows.
 */
function TablePreview({ text, name }: { text: string; name: string }) {
  const parsed = useMemo(() => {
    const delimiter = detectDelimiter(text, name);
    const { rows, truncated } = parseCsv(text, delimiter);
    const [header = [], ...body] = rows;
    return { header, body, truncated, numeric: numericColumns(body) };
  }, [text, name]);
  const [sort, setSort] = useState<{ col: number; desc: boolean } | null>(null);
  const [filter, setFilter] = useState("");

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    let rows = parsed.body.map((cells, i) => ({ cells, line: i + 1 }));
    if (needle) {
      rows = rows.filter((r) => r.cells.some((c) => c.toLowerCase().includes(needle)));
    }
    if (sort) {
      const numeric = parsed.numeric[sort.col] ?? false;
      rows = [...rows].sort((a, b) => {
        const order = compareCells(a.cells[sort.col] ?? "", b.cells[sort.col] ?? "", numeric);
        return sort.desc ? -order : order;
      });
    }
    return rows;
  }, [parsed, filter, sort]);

  if (parsed.header.length === 0) {
    return <p className="text-sm text-fgmuted">Empty file.</p>;
  }

  const toggleSort = (col: number) =>
    setSort((s) =>
      s?.col !== col ? { col, desc: false } : s.desc ? null : { col, desc: true }
    );

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-1.5 rounded-md border border-line px-2 py-1 focus-within:border-pegasus-400">
          <Search size={14} aria-hidden="true" className="text-fgsubtle" />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter rows"
            aria-label="Filter rows"
            className="w-48 bg-transparent text-xs text-fg outline-none placeholder:text-fgsubtle"
          />
        </label>
        <span className="text-xs text-fgsubtle">
          {filter ? `${shown.length.toLocaleString()} of ` : ""}
          {parsed.body.length.toLocaleString()} rows · {parsed.header.length} columns
          {parsed.truncated ? " (the rest not loaded)" : ""}
        </span>
      </div>
      <div className="max-h-[65vh] overflow-auto rounded border border-line">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 z-10 bg-surface shadow-[0_1px_0_var(--c-line,#e5e7eb)]">
            <tr>
              <th className="w-10 px-2 py-1.5 text-right font-normal text-fgsubtle">#</th>
              {parsed.header.map((cell, i) => (
                <th
                  key={i}
                  aria-sort={
                    sort?.col === i ? (sort.desc ? "descending" : "ascending") : "none"
                  }
                  className={cn("whitespace-nowrap px-2 py-1.5 font-medium text-fgmuted", parsed.numeric[i] && "text-right")}
                >
                  <button
                    onClick={() => toggleSort(i)}
                    className="inline-flex items-center gap-1 hover:text-fg"
                    title="Sort by this column"
                  >
                    {cell || `column ${i + 1}`}
                    {sort?.col === i &&
                      (sort.desc ? <ArrowDown size={12} /> : <ArrowUp size={12} />)}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {shown.slice(0, TABLE_DRAW).map((r) => (
              <tr key={r.line} className="hover:bg-base">
                <td className="px-2 py-1 text-right text-fgsubtle">{r.line}</td>
                {parsed.header.map((_, j) => (
                  <td
                    key={j}
                    title={r.cells[j]}
                    className={cn(
                      "max-w-[22rem] truncate whitespace-nowrap px-2 py-1 text-fg",
                      parsed.numeric[j] && "text-right tabular-nums"
                    )}
                  >
                    {r.cells[j] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {shown.length > TABLE_DRAW && (
        <p className="text-xs text-fgsubtle">
          Drawing the first {TABLE_DRAW.toLocaleString()} of{" "}
          {shown.length.toLocaleString()} rows. Sort or filter to see others, or
          download the file.
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
