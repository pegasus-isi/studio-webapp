"use client";

/**
 * Pick a file or folder from the workspace, or upload one (#241, Phase 3).
 *
 * This is where Phase 2's deferred piece lands: uploading straight into a run
 * parameter. It was deferred precisely because there was nothing to upload
 * *into* until a picker existed — the two only make sense together.
 *
 * The escape hatch is deliberate and permanent: the text box is always there
 * and always authoritative. Type inference is a heuristic, so a parameter
 * wrongly guessed to be a file must still accept whatever the user needs to
 * put in it. The picker assists; it never becomes the only way through.
 */

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import * as api from "@/lib/api/client";
import { cn } from "@/lib/utils";

interface Props {
  value: string;
  onChange: (value: string) => void;
  /** "file" restricts selection to files; "directory" to folders. */
  mode: "file" | "directory";
  id?: string;
  describedBy?: string;
  placeholder?: string;
}

export function FilePicker({
  value,
  onChange,
  mode,
  id,
  describedBy,
  placeholder,
}: Props) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <div className="flex gap-1.5">
        <input
          id={id}
          type="text"
          value={value}
          aria-describedby={describedBy}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          className="min-w-0 flex-1 rounded-md border border-line px-2 py-1 font-mono text-sm"
        />
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="shrink-0 rounded-md border border-line px-2 py-1 text-xs text-fgmuted hover:border-pegasus-400 hover:text-pegasus-700 dark:hover:text-pegasus-300"
        >
          Browse…
        </button>
      </div>
      {open && (
        <BrowseDialog
          mode={mode}
          onPick={(path) => {
            onChange(path);
            setOpen(false);
          }}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

function BrowseDialog({
  mode,
  onPick,
  onClose,
}: {
  mode: "file" | "directory";
  onPick: (path: string) => void;
  onClose: () => void;
}) {
  const { data: limit } = useQuery({
    queryKey: ["upload-limit"],
    queryFn: () => api.getUploadLimit(),
  });

  // Start in the data folder — it is where Phase 2 puts uploads, so it is
  // where a user's own inputs are. Falls back to the workspace root.
  const [cwd, setCwd] = useState<string>("");
  useEffect(() => {
    if (limit?.data_dir && !cwd) setCwd(limit.data_dir);
  }, [limit, cwd]);

  const {
    data: listing,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ["browse", cwd],
    enabled: !!cwd,
    queryFn: () => api.listFiles(cwd),
    retry: false,
  });

  // The workspace root, derived from the data dir the API reports. Browsing
  // above it is refused server-side (_safe_path), so stopping here turns a
  // 403 dead end into an "Up a level" that simply isn't offered.
  const root = useMemo(() => {
    if (!limit?.data_dir) return null;
    const trimmed = limit.data_dir.replace(/\/+$/, "");
    const idx = trimmed.lastIndexOf("/");
    return idx > 0 ? trimmed.slice(0, idx) : null;
  }, [limit]);

  const parent = useMemo(() => {
    if (!cwd || !root) return null;
    const trimmed = cwd.replace(/\/+$/, "");
    if (trimmed === root) return null; // already at the top of the workspace
    const idx = trimmed.lastIndexOf("/");
    if (idx <= 0) return null;
    const up = trimmed.slice(0, idx);
    return up.length >= root.length ? up : null;
  }, [cwd, root]);

  const entries = listing?.entries ?? [];
  const dirs = entries.filter((e) => e.type === "dir");
  const files = mode === "file" ? entries.filter((e) => e.type === "file") : [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6"
      role="dialog"
      aria-modal="true"
      aria-label={mode === "file" ? "Choose a file" : "Choose a folder"}
      onClick={onClose}
    >
      <div
        className="flex max-h-[80vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-line bg-surface"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-line px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-fg">
              {mode === "file" ? "Choose a file" : "Choose a folder"}
            </h2>
            <button
              onClick={onClose}
              aria-label="Close"
              className="rounded px-2 py-0.5 text-sm text-fgmuted hover:bg-base hover:text-fg"
            >
              ✕
            </button>
          </div>
          <p className="mt-1 truncate font-mono text-xs text-fgsubtle" title={cwd}>
            {cwd || "…"}
          </p>
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          {error ? (
            <p className="p-4 text-sm text-fgmuted">
              Can&apos;t open this folder. Go up a level and try another.
            </p>
          ) : isLoading ? (
            <p className="p-4 text-sm text-fgmuted">Loading…</p>
          ) : (
            <ul className="divide-y divide-line">
              {parent && (
                <li>
                  <button
                    onClick={() => setCwd(parent)}
                    className="w-full px-4 py-2 text-left text-sm text-fgmuted hover:bg-base"
                  >
                    ↑ Up a level
                  </button>
                </li>
              )}
              {dirs.map((entry) => (
                <li key={entry.name} className="flex items-center gap-2 pr-3">
                  <button
                    onClick={() => setCwd(`${cwd.replace(/\/+$/, "")}/${entry.name}`)}
                    className="min-w-0 flex-1 truncate px-4 py-2 text-left text-sm text-fg hover:bg-base"
                  >
                    📁 {entry.name}
                  </button>
                  {mode === "directory" && (
                    <button
                      onClick={() =>
                        onPick(`${cwd.replace(/\/+$/, "")}/${entry.name}`)
                      }
                      className="shrink-0 rounded border border-line px-2 py-0.5 text-xs text-fgmuted hover:border-pegasus-400 hover:text-pegasus-700 dark:hover:text-pegasus-300"
                    >
                      Use this
                    </button>
                  )}
                </li>
              ))}
              {files.map((entry) => (
                <li key={entry.name}>
                  <button
                    onClick={() => onPick(`${cwd.replace(/\/+$/, "")}/${entry.name}`)}
                    className="w-full truncate px-4 py-2 text-left text-sm text-fg hover:bg-base"
                  >
                    📄 {entry.name}
                  </button>
                </li>
              ))}
              {dirs.length === 0 && files.length === 0 && (
                <li className="px-4 py-6 text-center text-sm text-fgmuted">
                  Nothing here.
                </li>
              )}
            </ul>
          )}
        </div>

        <div className="border-t border-line px-4 py-3">
          {mode === "directory" && cwd && (
            <button
              onClick={() => onPick(cwd)}
              className="mb-2 w-full rounded-md bg-pegasus-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-pegasus-700"
            >
              Use this folder
            </button>
          )}
          {mode === "file" && (
            <InlineUpload dest={cwd} onUploaded={onPick} onDone={refetch} />
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Upload into the folder currently being browsed, then select the result.
 *
 * The deferred half of Phase 2: "add my file" and "use my file" are one
 * action for a scientist, and making them two screens is the friction the
 * redesign is trying to remove.
 */
function InlineUpload({
  dest,
  onUploaded,
  onDone,
}: {
  dest: string;
  onUploaded: (path: string) => void;
  onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div>
      <label
        className={cn(
          "block cursor-pointer rounded-md border border-dashed border-line px-3 py-2 text-center text-xs text-fgmuted hover:border-pegasus-400 hover:text-fg",
          busy && "pointer-events-none opacity-60"
        )}
      >
        {busy ? "Uploading…" : "Or upload a file from your computer"}
        <input
          type="file"
          className="sr-only"
          disabled={busy}
          onChange={async (e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (!file) return;
            setBusy(true);
            setError(null);
            try {
              const result = await api.uploadFile(file, { dest });
              onDone();
              // Select what actually landed — the server renames on collision,
              // so echoing the picked name would point at the wrong file.
              onUploaded(result.path);
            } catch (err) {
              setError((err as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        />
      </label>
      {error && (
        <p className="mt-1 text-xs text-red-600 dark:text-rose-400">{error}</p>
      )}
    </div>
  );
}
