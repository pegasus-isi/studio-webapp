"use client";

/**
 * A run's results (#241, Phase 4b).
 *
 * The reason a scientist ran anything. Before this, Studio never showed a run's
 * output files at all — finding them meant leaving for JupyterLab and knowing
 * where Pegasus stages results. This is the panel that ends that.
 *
 * Preview decisions come from the API (`previewable`), not from re-deriving a
 * size threshold here: two copies of that rule would drift, and the one that
 * drifts wrong hangs someone's browser on a multi-gigabyte file.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { OutputFile } from "@/lib/api/client";
import * as api from "@/lib/api/client";
import { jupyterUrl } from "@/lib/urls";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 100;

function formatBytes(bytes: number): string {
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

const KIND_ICON: Record<OutputFile["kind"], string> = {
  image: "🖼",
  table: "🔢",
  text: "📄",
  binary: "📦",
};

export function OutputsPanel({
  runId,
  runFinished,
}: {
  runId: string;
  /** A finished run's outputs are final; a running one's are still arriving. */
  runFinished: boolean;
}) {
  const [offset, setOffset] = useState(0);
  const [preview, setPreview] = useState<OutputFile | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["run-outputs", runId, offset],
    queryFn: () => api.getRunOutputs(runId, { offset, limit: PAGE_SIZE }),
    // While a run is going, files keep appearing; once it's done they don't.
    refetchInterval: runFinished ? false : 10_000,
  });

  if (isLoading) {
    return <p className="p-6 text-sm text-fgmuted">Looking for results…</p>;
  }

  if (error) {
    return (
      <p className="p-6 text-sm text-red-600 dark:text-rose-400">
        Couldn&apos;t load results: {(error as Error).message}
      </p>
    );
  }

  // The three empty states are genuinely different and must not be collapsed
  // into one "no results" message — see `found_via` in the API.
  if (!data || data.total === 0) {
    if (data?.found_via === "none") {
      return (
        <div className="p-6">
          <p className="text-sm text-fg">
            We couldn&apos;t tell where this workflow puts its results.
          </p>
          <p className="mt-1 text-sm text-fgmuted">
            That doesn&apos;t mean there aren&apos;t any — this workflow
            doesn&apos;t declare an output location, so we don&apos;t know where
            to look. You can browse your workspace in{" "}
            <a
              href={jupyterUrl("/lab")}
              target="_blank"
              rel="noopener noreferrer"
              className="text-pegasus-600 hover:underline dark:text-pegasus-300"
            >
              JupyterLab
            </a>{" "}
            to find them.
          </p>
        </div>
      );
    }
    return (
      <div className="p-6">
        <p className="text-sm text-fg">
          {runFinished
            ? "This run didn't produce any result files."
            : "No results yet."}
        </p>
        <p className="mt-1 text-sm text-fgmuted">
          {runFinished
            ? "Results are saved to "
            : "Files will appear here as your steps finish. They're saved to "}
          <span className="font-mono text-xs">{data?.output_dir}</span>.
        </p>
      </div>
    );
  }

  const showingTo = Math.min(offset + data.files.length, data.total);

  return (
    <div className="p-6">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm text-fgmuted">
          {data.total} file{data.total === 1 ? "" : "s"}
          {!runFinished && " so far"}
          {data.truncated && " (showing the first " + data.total + ")"}
        </p>
        <a
          href={jupyterUrl("/lab")}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs font-medium text-pegasus-600 hover:underline dark:text-pegasus-300"
        >
          Open workspace in JupyterLab ↗
        </a>
      </div>

      <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
        {data.files.map((file) => (
          <li
            key={file.path}
            className="flex items-center gap-3 px-4 py-2.5 hover:bg-base"
          >
            <span aria-hidden="true">{KIND_ICON[file.kind]}</span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm text-fg" title={file.path}>
                {file.name}
              </span>
              {file.path !== file.name && (
                <span className="block truncate text-xs text-fgsubtle">
                  {file.path}
                </span>
              )}
            </span>
            <span className="shrink-0 text-xs text-fgmuted">
              {formatBytes(file.size)}
            </span>
            {file.previewable && (
              <button
                onClick={() => setPreview(file)}
                className="shrink-0 rounded border border-line px-2 py-0.5 text-xs text-fgmuted hover:border-pegasus-400 hover:text-pegasus-700 dark:hover:text-pegasus-300"
              >
                Preview
              </button>
            )}
            <a
              href={api.runOutputFileUrl(runId, file.path, true)}
              download={file.name}
              className="shrink-0 rounded border border-line px-2 py-0.5 text-xs text-fgmuted hover:border-pegasus-400 hover:text-pegasus-700 dark:hover:text-pegasus-300"
            >
              Download
            </a>
          </li>
        ))}
      </ul>

      {data.total > PAGE_SIZE && (
        <div className="mt-3 flex items-center gap-3 text-sm">
          <button
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            disabled={offset === 0}
            className="rounded border border-line px-2 py-1 text-xs text-fgmuted disabled:opacity-40"
          >
            Previous
          </button>
          <span className="text-xs text-fgmuted">
            {offset + 1}–{showingTo} of {data.total}
          </span>
          <button
            onClick={() => setOffset(offset + PAGE_SIZE)}
            disabled={showingTo >= data.total}
            className="rounded border border-line px-2 py-1 text-xs text-fgmuted disabled:opacity-40"
          >
            Next
          </button>
        </div>
      )}

      {preview && (
        <PreviewDialog
          runId={runId}
          file={preview}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  );
}

function PreviewDialog({
  runId,
  file,
  onClose,
}: {
  runId: string;
  file: OutputFile;
  onClose: () => void;
}) {
  const url = api.runOutputFileUrl(runId, file.path);
  const { data: text, isLoading } = useQuery({
    queryKey: ["run-output-preview", runId, file.path],
    // Images render from the URL directly; only text and tables need fetching.
    enabled: file.kind === "text" || file.kind === "table",
    queryFn: async () => {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Couldn't read the file (${res.status})`);
      return res.text();
    },
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6"
      role="dialog"
      aria-modal="true"
      aria-label={`Preview of ${file.name}`}
      onClick={onClose}
    >
      <div
        className="flex max-h-full w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-line bg-surface"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <span className="min-w-0 truncate text-sm font-medium text-fg">
            {file.path}
          </span>
          <span className="flex shrink-0 items-center gap-2">
            <a
              href={api.runOutputFileUrl(runId, file.path, true)}
              download={file.name}
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
          {file.kind === "image" ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={url}
              alt={file.name}
              className="mx-auto max-h-[70vh] max-w-full object-contain"
            />
          ) : isLoading ? (
            <p className="text-sm text-fgmuted">Loading…</p>
          ) : file.kind === "table" ? (
            <TablePreview text={text ?? ""} />
          ) : (
            <pre className="whitespace-pre-wrap [overflow-wrap:anywhere] font-mono text-xs text-fg">
              {text}
            </pre>
          )}
        </div>
      </div>
    </div>
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
