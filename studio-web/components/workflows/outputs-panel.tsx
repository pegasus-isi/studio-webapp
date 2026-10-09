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
import { appRelativePath } from "@/lib/urls";
import {
  FilePreviewDialog,
  FileTypeIcon,
  formatBytes,
} from "@/components/workflows/file-preview";

const PAGE_SIZE = 100;

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
              href={appRelativePath("/notebooks")}
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
          href={appRelativePath("/notebooks")}
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
            <FileTypeIcon name={file.name} kind={file.kind} />
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
        <FilePreviewDialog
          target={{
            name: preview.name,
            path: preview.path,
            kind: preview.kind,
            size: preview.size,
            partial: preview.partial,
            url: api.runOutputFileUrl(runId, preview.path),
            downloadUrl: api.runOutputFileUrl(runId, preview.path, true),
          }}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  );
}
