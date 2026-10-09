"use client";

import { useEffect, useRef, useState } from "react";

/** Mirrors the server's rule in services/project_rename.py, so most mistakes
 *  are caught while typing; the server still has the final word. */
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/;

interface Props {
  currentName: string;
  busy: boolean;
  /** The server's refusal, shown in the dialog rather than as a toast, so the
   *  user can fix the name without starting over. */
  error: string | null;
  onRename: (name: string) => void;
  onCancel: () => void;
}

/**
 * Rename a workflow's folder.
 *
 * The caveat is stated up front because it is the one thing a move cannot
 * fix: Pegasus writes absolute paths into every run, so past runs stay
 * viewable but cannot be resumed in place once their folder has moved.
 */
export function RenameProjectDialog({
  currentName,
  busy,
  error,
  onRename,
  onCancel,
}: Props) {
  const [name, setName] = useState(currentName);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.select();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, busy]);

  const trimmed = name.trim();
  const unchanged = trimmed === currentName;
  const invalid = trimmed !== "" && !NAME_RE.test(trimmed);
  const canSubmit = !busy && trimmed !== "" && !unchanged && !invalid;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={() => !busy && onCancel()}
    >
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="rename-project-title"
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault();
          if (canSubmit) onRename(trimmed);
        }}
        className="w-full max-w-md rounded-xl border border-line bg-surface p-5 shadow-xl"
      >
        <h3 id="rename-project-title" className="text-base font-semibold text-fg">
          Rename folder
        </h3>
        <p className="mt-2 text-sm leading-relaxed text-fgmuted">
          Renames the workflow&apos;s folder in your workspace. Past runs stay
          listed, but an old run can&apos;t be resumed in place afterwards,
          because Pegasus recorded the old path. Not possible while a run is
          in progress.
        </p>
        <label className="mt-4 block text-xs font-medium text-fgmuted">
          New folder name
          <input
            ref={inputRef}
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={busy}
            spellCheck={false}
            autoComplete="off"
            className="mt-1 w-full rounded-md border border-line bg-base px-3 py-2 font-mono text-sm text-fg outline-none focus:border-pegasus-400 disabled:opacity-50"
          />
        </label>
        {invalid && (
          <p className="mt-1.5 text-xs text-amber-700 dark:text-amber-400">
            Letters, numbers, &apos;-&apos;, &apos;_&apos; or &apos;.&apos;,
            starting with a letter or number. No spaces or slashes.
          </p>
        )}
        {error && (
          <p role="alert" className="mt-1.5 text-xs text-red-600 dark:text-rose-400">
            {error}
          </p>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-md border border-line bg-surface px-4 py-2 text-sm font-medium text-fg hover:bg-base disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded-md bg-pegasus-600 px-4 py-2 text-sm font-semibold text-white hover:bg-pegasus-700 disabled:opacity-50"
          >
            {busy ? "Renaming…" : "Rename"}
          </button>
        </div>
      </form>
    </div>
  );
}
