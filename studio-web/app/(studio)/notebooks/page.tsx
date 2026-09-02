"use client";

/**
 * JupyterLab, in Studio.
 *
 * JupyterLab runs as an always-on service started with the container; this
 * page does not launch it. It used to only *link* to it — and auto-open a new
 * tab on arrival, which popup blockers eat and which loses you the rest of
 * Studio. Embedding it keeps the rail, the assistant and your analyses one
 * click away while you are in a notebook.
 *
 * The server side was already set up for this and nobody had used it:
 * `docker/jupyter/jupyter_server_config.py` sends
 * `frame-ancestors 'self' *`, disables XSRF and allows any origin,
 * specifically so Studio can frame it. Studio and `/jupyter/*` are the same
 * origin behind nginx on :8888, so no header relaxation is needed here.
 *
 * "Open in new tab" stays, because a notebook is the one thing in Studio you
 * genuinely want on a second monitor.
 */

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useWorkspaceStore } from "@/lib/stores/workspace-store";
import * as api from "@/lib/api/client";
import { jupyterUrl } from "@/lib/urls";

export default function NotebooksPage() {
  const jupyterStatus = useWorkspaceStore((s) => s.jupyterStatus);
  const setJupyterStatus = useWorkspaceStore((s) => s.setJupyterStatus);
  // Bumping this is how Reload works: changing the iframe key remounts it,
  // which is a clean reload without reaching into its contentWindow (and
  // without the browser treating it as a history entry).
  const [reloadKey, setReloadKey] = useState(0);
  const runningRef = useRef(false);

  // `?path=` opens a specific folder or file — how "Open in JupyterLab" on a
  // workflow and "Files" on a run get here instead of spawning a tab. The
  // value comes from the API's `jupyter_path` (already relative to Jupyter's
  // root), and each segment is encoded so a space or a "#" in a run directory
  // name cannot break the URL.
  const params = useSearchParams();
  const path = params.get("path") ?? "";
  const src = path
    ? jupyterUrl(
        `/lab/tree/${path
          .split("/")
          .filter(Boolean)
          .map(encodeURIComponent)
          .join("/")}`
      )
    : jupyterUrl("/lab");
  const running = jupyterStatus === "running";
  runningRef.current = running;

  const openInTab = () => window.open(src, "_blank", "noopener");

  // Poll until the service is up, then stop. It starts with the container, so
  // this is a first-paint race rather than an ongoing condition — polling a
  // healthy service every 3s for as long as the page is open buys nothing.
  useEffect(() => {
    if (running) return;
    let active = true;
    const poll = async () => {
      try {
        const res = await api.getJupyterStatus();
        if (!active) return;
        setJupyterStatus(
          res.status as "stopped" | "starting" | "running",
          res.port
        );
      } catch {
        /* keep polling — the service may still be coming up */
      }
    };
    poll();
    const id = setInterval(poll, 3000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [running, setJupyterStatus]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface px-4 py-2">
        <h1 className="text-sm font-semibold text-fg">Notebooks</h1>
        <span className="truncate text-xs text-fgsubtle">
          {path || "JupyterLab, in the same workspace as your analyses"}
        </span>

        <div className="ml-auto flex items-center gap-1.5">
          <button
            onClick={() => setReloadKey((k) => k + 1)}
            disabled={!running}
            title="Reload JupyterLab"
            className="rounded-lg border border-line px-2.5 py-1 text-xs font-medium text-fgmuted hover:text-fg disabled:opacity-40"
          >
            Reload
          </button>
          <button
            onClick={openInTab}
            disabled={!running}
            title="Open JupyterLab in a new browser tab"
            className="flex items-center gap-1 rounded-lg border border-line px-2.5 py-1 text-xs font-medium text-fgmuted hover:border-pegasus-400 hover:text-pegasus-700 disabled:opacity-40 dark:hover:text-pegasus-300"
          >
            Open in new tab
            <span aria-hidden="true">↗</span>
          </button>
        </div>
      </div>

      {running ? (
        <iframe
          key={`${reloadKey}:${src}`}
          src={src}
          title="JupyterLab"
          // Same origin behind nginx, so no sandbox: JupyterLab needs
          // scripts, storage, downloads and its own service worker, and a
          // sandbox attribute here would break kernels rather than contain
          // anything we don't already trust.
          className="min-h-0 flex-1 border-0 bg-base"
          allow="clipboard-read; clipboard-write"
        />
      ) : (
        <div className="flex flex-1 items-center justify-center">
          <div className="text-center">
            <div
              aria-hidden="true"
              className="mx-auto h-8 w-8 animate-spin rounded-full border-4 border-pegasus-200 border-t-pegasus-600"
            />
            <p className="mt-3 text-sm text-fgmuted">
              Starting JupyterLab…
            </p>
            <p className="mt-1 text-xs text-fgsubtle">
              It starts automatically with your workspace.
            </p>
            <button
              onClick={openInTab}
              className="mt-4 text-xs font-semibold text-pegasus-700 hover:underline dark:text-pegasus-300"
            >
              Try opening it in a new tab instead
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
