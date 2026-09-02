"use client";

/**
 * My data — where a scientist's own files live (#241, Phase 2).
 *
 * The Runner persona's task begins "run this analysis on my data", and until
 * now there was no way to get that data in without JupyterLab or scp. This page
 * is the answer to both "how do I add my file" and "what did I already add".
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import * as api from "@/lib/api/client";
import {
  UploadDropZone,
  formatBytes,
} from "@/components/data/upload-drop-zone";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { appRelativePath } from "@/lib/urls";

export default function DataPage() {
  const qc = useQueryClient();
  const [copied, setCopied] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<{ path: string; name: string } | null>(
    null
  );
  const [deleting, setDeleting] = useState(false);

  const { data: limit } = useQuery({
    queryKey: ["upload-limit"],
    queryFn: () => api.getUploadLimit(),
  });

  const dataDir = limit?.data_dir;
  const { data: listing, isLoading } = useQuery({
    queryKey: ["data-files", dataDir],
    enabled: !!dataDir,
    queryFn: () => api.listFiles(dataDir!),
    // The directory may not exist until the first upload; an error here is a
    // normal empty state, not a failure to report.
    retry: false,
  });

  const files = (listing?.entries ?? []).filter((e) => e.type === "file");

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["data-files"] });
    qc.invalidateQueries({ queryKey: ["upload-limit"] });
  };

  const copyPath = async (name: string) => {
    if (!dataDir) return;
    const full = `${dataDir}/${name}`;
    try {
      await navigator.clipboard.writeText(full);
      setCopied(name);
      setTimeout(() => setCopied((c) => (c === name ? null : c)), 2000);
    } catch {
      /* clipboard blocked — the path is visible in the row regardless */
    }
  };

  const handleDelete = async () => {
    if (!toDelete) return;
    setDeleting(true);
    try {
      await api.deleteFile(toDelete.path);
      refresh();
    } finally {
      setDeleting(false);
      setToDelete(null);
    }
  };

  return (
    <div className="h-full overflow-auto">
      <div className="border-b border-line bg-surface px-6 py-4">
        <h1 className="text-lg font-semibold text-fg">My data</h1>
        <p className="text-sm text-fgmuted">
          Files you add here stay in your workspace and can be used as inputs to
          any analysis.
        </p>
      </div>

      <div className="mx-auto max-w-3xl px-6 py-6">
        <UploadDropZone limit={limit} onUploaded={refresh} />

        <div className="mt-8">
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-sm font-medium uppercase tracking-wider text-fgsubtle">
              Your files
            </h2>
            <a
              href={appRelativePath("/notebooks")}
                            className="text-xs font-medium text-pegasus-600 hover:underline dark:text-pegasus-300"
            >
              Browse everything in JupyterLab ↗
            </a>
          </div>

          {isLoading ? (
            <p className="text-sm text-fgmuted">Loading…</p>
          ) : files.length === 0 ? (
            <p className="rounded-lg border border-line bg-surface px-4 py-6 text-center text-sm text-fgmuted">
              Nothing here yet. Add a file above and it will show up here.
            </p>
          ) : (
            <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
              {files.map((file) => (
                <li
                  key={file.name}
                  className="flex items-center gap-3 px-4 py-2.5 hover:bg-base"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-fg">
                      {file.name}
                    </span>
                    <span className="block truncate font-mono text-xs text-fgsubtle">
                      {dataDir}/{file.name}
                    </span>
                  </span>
                  <span className="shrink-0 text-xs text-fgmuted">
                    {file.size != null ? formatBytes(file.size) : "—"}
                  </span>
                  {/* Until the run form has a real file picker (Phase 3), the
                      practical way to use a file is to paste its path into a
                      parameter — so make that one click rather than a
                      transcription exercise. */}
                  <button
                    onClick={() => copyPath(file.name)}
                    title="Copy the full path, to paste into a workflow input"
                    className="shrink-0 rounded border border-line px-2 py-0.5 text-xs text-fgmuted hover:border-pegasus-400 hover:text-pegasus-700 dark:hover:text-pegasus-300"
                  >
                    {copied === file.name ? "Copied" : "Copy path"}
                  </button>
                  <button
                    onClick={() =>
                      setToDelete({
                        path: `${dataDir}/${file.name}`,
                        name: file.name,
                      })
                    }
                    aria-label={`Delete ${file.name}`}
                    className="shrink-0 rounded border border-line px-2 py-0.5 text-xs text-fgmuted hover:border-red-400 hover:text-red-600 dark:hover:text-red-400"
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {toDelete && (
        <ConfirmDialog
          title="Delete this file?"
          message={`This permanently removes "${toDelete.name}" from your workspace. Any analysis that uses it as an input will fail until you add it again. This cannot be undone.`}
          confirmLabel="Delete file"
          destructive
          busy={deleting}
          onConfirm={handleDelete}
          onCancel={() => setToDelete(null)}
        />
      )}
    </div>
  );
}
