"use client";

/**
 * Drag-and-drop (or browse) upload (#241, Phase 2).
 *
 * Studio had no way to get data in: a scientist's files are on their laptop,
 * and the only routes were JupyterLab's uploader or scp — the escape hatches
 * this redesign demotes. Without this the Runner persona cannot start their
 * task at all, which made every other phase moot for them.
 */

import { useCallback, useRef, useState } from "react";
import * as api from "@/lib/api/client";
import { cn } from "@/lib/utils";

interface QueuedUpload {
  id: string;
  name: string;
  size: number;
  progress: number;
  status: "uploading" | "done" | "error";
  error?: string;
  landedAs?: string;
}

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

export function UploadDropZone({
  dest,
  limit,
  onUploaded,
}: {
  /** Workspace-relative destination; omitted means the default data folder. */
  dest?: string;
  limit: api.UploadLimit | undefined;
  onUploaded?: (file: api.UploadedFile) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const [queue, setQueue] = useState<QueuedUpload[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const counter = useRef(0);

  const send = useCallback(
    async (files: FileList | File[]) => {
      for (const file of Array.from(files)) {
        const id = `u${counter.current++}`;

        // Check the size *before* sending. A limit enforced only after the
        // bytes arrive is not a check; on a slow connection it's a way to
        // waste somebody's afternoon and tell them afterwards.
        if (limit && file.size > limit.max_bytes) {
          setQueue((q) => [
            ...q,
            {
              id,
              name: file.name,
              size: file.size,
              progress: 0,
              status: "error",
              error: `Too large to upload here (${formatBytes(
                file.size
              )}, limit ${formatBytes(limit.max_bytes)}).`,
            },
          ]);
          continue;
        }

        setQueue((q) => [
          ...q,
          { id, name: file.name, size: file.size, progress: 0, status: "uploading" },
        ]);

        try {
          const result = await api.uploadFile(file, {
            dest,
            onProgress: (fraction) =>
              setQueue((q) =>
                q.map((u) => (u.id === id ? { ...u, progress: fraction } : u))
              ),
          });
          setQueue((q) =>
            q.map((u) =>
              u.id === id
                ? {
                    ...u,
                    status: "done",
                    progress: 1,
                    // The server renames on collision, so show what it became
                    // rather than what was picked — otherwise a user looks for
                    // "data.csv" and finds "data (2).csv".
                    landedAs: result.name,
                  }
                : u
            )
          );
          onUploaded?.(result);
        } catch (e) {
          setQueue((q) =>
            q.map((u) =>
              u.id === id
                ? { ...u, status: "error", error: (e as Error).message }
                : u
            )
          );
        }
      }
    },
    [dest, limit, onUploaded]
  );

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (e.dataTransfer.files.length) send(e.dataTransfer.files);
        }}
        className={cn(
          "rounded-xl border-2 border-dashed p-8 text-center transition-colors",
          dragging
            ? "border-pegasus-500 bg-pegasus-50 dark:bg-pegasus-400/10"
            : "border-line bg-surface"
        )}
      >
        <p className="text-sm font-medium text-fg">
          Drag files here to add them to your workspace
        </p>
        <p className="mt-1 text-sm text-fgmuted">
          or{" "}
          <button
            onClick={() => inputRef.current?.click()}
            className="font-medium text-pegasus-600 underline-offset-2 hover:underline dark:text-pegasus-300"
          >
            choose files
          </button>
        </p>
        {limit && (
          <p className="mt-3 text-xs text-fgsubtle">
            Up to {formatBytes(limit.max_bytes)} per file.
          </p>
        )}
        <input
          ref={inputRef}
          type="file"
          multiple
          className="sr-only"
          onChange={(e) => {
            if (e.target.files?.length) send(e.target.files);
            e.target.value = ""; // let the same file be picked again
          }}
        />
      </div>

      {queue.length > 0 && (
        <ul className="mt-3 space-y-2">
          {queue.map((item) => (
            <li
              key={item.id}
              className="rounded-lg border border-line bg-surface px-3 py-2"
            >
              <div className="flex items-center gap-3">
                <span className="min-w-0 flex-1 truncate text-sm text-fg">
                  {item.name}
                </span>
                <span className="shrink-0 text-xs text-fgmuted">
                  {formatBytes(item.size)}
                </span>
              </div>

              {item.status === "uploading" && (
                <div className="mt-1.5 h-1.5 w-full rounded-full bg-muted">
                  <div
                    className="h-1.5 rounded-full bg-pegasus-600 transition-all"
                    style={{ width: `${Math.round(item.progress * 100)}%` }}
                  />
                </div>
              )}

              {item.status === "done" && (
                <p className="mt-1 text-xs text-green-600 dark:text-emerald-400">
                  Added to your workspace
                  {item.landedAs && item.landedAs !== item.name && (
                    <> as “{item.landedAs}” (a file with that name already existed)</>
                  )}
                </p>
              )}

              {item.status === "error" && (
                <p className="mt-1 text-xs text-red-600 dark:text-rose-400">
                  {item.error}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* Large-data honesty: say what to do instead, rather than letting
          someone discover the ceiling by failing against it. */}
      {limit && (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs text-fgmuted hover:text-fg">
            Working with files bigger than {formatBytes(limit.max_bytes)}?
          </summary>
          <p className="mt-2 text-xs leading-relaxed text-fgmuted">
            Browser uploads aren&apos;t practical at that size. Copy the data
            straight into your workspace instead — over SSH/SCP if you have
            terminal access, or by having your workflow download it as its first
            step, which also makes the analysis reproducible for anyone else who
            runs it.
          </p>
        </details>
      )}
    </div>
  );
}
