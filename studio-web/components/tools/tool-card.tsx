"use client";

import type { ToolDetail } from "@/lib/api/client";
import {
  useInstallTool,
  useUninstallTool,
  useStartTool,
  useStopTool,
} from "@/lib/hooks/use-tools";
import { useToolStore } from "@/lib/stores/tool-store";
import { cn, statusColor } from "@/lib/utils";

interface Props {
  tool: ToolDetail;
}

export function ToolCard({ tool }: Props) {
  const { info, installed, status } = tool;
  const install = useInstallTool();
  const uninstall = useUninstallTool();
  const start = useStartTool();
  const stop = useStopTool();
  const addTab = useToolStore((s) => s.addTab);
  const installing = useToolStore((s) => s.installingTools.has(info.id));
  const setInstalling = useToolStore((s) => s.setInstalling);

  const isRunning = status === "running";
  const isTerminal = info.type === "terminal";
  const busy =
    install.isPending ||
    uninstall.isPending ||
    start.isPending ||
    stop.isPending ||
    installing;

  const handleInstall = async () => {
    setInstalling(info.id, true);
    try {
      await install.mutateAsync(info.id);
    } finally {
      setInstalling(info.id, false);
    }
  };

  const handleStart = async () => {
    await start.mutateAsync(info.id);
    if (isTerminal) {
      addTab(info.id, info.name);
    }
  };

  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <div className="flex items-start justify-between">
        <div>
          <h3 className="flex items-center gap-1.5 font-medium text-fg">
            {info.name}
            {info.paid && (
              <span
                title="Runs on your own LLM API tokens"
                className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700 dark:bg-amber-500/15 dark:text-amber-300"
              >
                Paid
              </span>
            )}
          </h3>
          <p className="text-xs text-fgmuted">{info.vendor}</p>
        </div>
        {status && (
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-xs font-medium",
              statusColor(status)
            )}
          >
            {status}
          </span>
        )}
      </div>

      <p className="mt-1 text-xs text-fgmuted line-clamp-2">
        {info.description}
      </p>

      <div className="mt-3 flex gap-2">
        {!installed ? (
          <button
            onClick={handleInstall}
            disabled={busy}
            className="flex-1 rounded-md bg-pegasus-600 px-2 py-1 text-xs text-white hover:bg-pegasus-700 disabled:opacity-50"
          >
            {installing ? "Installing..." : "Install"}
          </button>
        ) : isRunning ? (
          <>
            {isTerminal && (
              <button
                onClick={() => addTab(info.id, info.name)}
                className="flex-1 rounded-md border border-pegasus-300 px-2 py-1 text-xs text-pegasus-700 dark:text-pegasus-300 hover:bg-pegasus-50 dark:bg-pegasus-400/10"
              >
                Open
              </button>
            )}
            <button
              onClick={() => stop.mutateAsync(info.id)}
              disabled={busy}
              className="rounded-md border border-red-300 px-2 py-1 text-xs text-red-700 dark:text-rose-400 hover:bg-red-50 dark:bg-rose-500/10 disabled:opacity-50"
            >
              Stop
            </button>
          </>
        ) : (
          <>
            <button
              onClick={handleStart}
              disabled={busy}
              className="flex-1 rounded-md bg-pegasus-600 px-2 py-1 text-xs text-white hover:bg-pegasus-700 disabled:opacity-50"
            >
              Start
            </button>
            <button
              onClick={() => uninstall.mutateAsync(info.id)}
              disabled={busy}
              className="rounded-md border border-line px-2 py-1 text-xs text-fgmuted hover:bg-base disabled:opacity-50"
            >
              Remove
            </button>
          </>
        )}
      </div>
    </div>
  );
}
