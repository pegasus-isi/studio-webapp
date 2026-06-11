"use client";

import { useEffect, useRef } from "react";
import { useWorkspaceStore } from "@/lib/stores/workspace-store";
import * as api from "@/lib/api/client";
import { jupyterUrl } from "@/lib/urls";

// JupyterLab runs as an always-on service started with the container. This page
// does NOT launch it — it only opens the running instance in a new browser tab.
export default function NotebooksPage() {
  const jupyterStatus = useWorkspaceStore((s) => s.jupyterStatus);
  const setJupyterStatus = useWorkspaceStore((s) => s.setJupyterStatus);
  const openedRef = useRef(false);

  const openInTab = () => window.open(jupyterUrl("/lab"), "_blank", "noopener");

  // Poll status; auto-open once when the service is up.
  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const res = await api.getJupyterStatus();
        if (!active) return;
        setJupyterStatus(
          res.status as "stopped" | "starting" | "running",
          res.port
        );
        if (res.status === "running" && !openedRef.current) {
          openedRef.current = true;
          openInTab();
        }
      } catch {
        /* keep polling */
      }
    };
    poll();
    const id = setInterval(poll, 3000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [setJupyterStatus]);

  const running = jupyterStatus === "running";

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-200 bg-white px-6 py-3">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Notebooks</h1>
          <p className="text-sm text-gray-500">JupyterLab environment</p>
        </div>
        {running && (
          <button
            onClick={openInTab}
            className="rounded-md bg-pegasus-600 px-3 py-1.5 text-sm text-white hover:bg-pegasus-700"
            title="Open JupyterLab in a new browser tab"
          >
            Open in Tab &rarr;
          </button>
        )}
      </div>

      {/* Body */}
      <div className="flex flex-1 items-center justify-center">
        {running ? (
          <div className="text-center">
            <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-green-50">
              <span className="text-3xl">&#x1F4D3;</span>
            </div>
            <h2 className="text-lg font-medium text-gray-900">
              JupyterLab is running
            </h2>
            <p className="mt-1 text-sm text-gray-500">
              Opens in a new browser tab.
            </p>
            <button
              onClick={openInTab}
              className="mt-4 rounded-md bg-pegasus-600 px-4 py-2 text-sm text-white hover:bg-pegasus-700"
            >
              Open JupyterLab
            </button>
          </div>
        ) : (
          <div className="text-center">
            <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-4 border-pegasus-200 border-t-pegasus-600" />
            <p className="text-sm text-gray-500">
              Waiting for the JupyterLab service&hellip;
            </p>
            <p className="mt-2 text-xs text-gray-400">
              It starts automatically with the container.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
