"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import type { MarketplaceWorkflow } from "@/lib/api/client";
import * as api from "@/lib/api/client";
import { appRelativePath } from "@/lib/urls";
import { AppLink } from "@/components/layout/app-link";

/** Tags for a workflow, tolerating an older API that only sends `topic`. */
function topicsOf(w: MarketplaceWorkflow): string[] {
  if (w.topics?.length) return w.topics;
  return w.topic ? [w.topic] : [];
}

function TopicChip({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={
        "rounded-full border px-3 py-1 text-xs font-medium capitalize " +
        (active
          ? "border-pegasus-500 bg-pegasus-50 text-pegasus-700 dark:bg-pegasus-400/10 dark:text-pegasus-300"
          : "border-line text-fgmuted hover:bg-base")
      }
    >
      {label}
      <span className="ml-1 font-mono text-[10px] text-fgsubtle">{count}</span>
    </button>
  );
}

export default function MarketplacePage() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["marketplace"],
    queryFn: () => api.getMarketplace(),
  });
  const [added, setAdded] = useState<Record<string, boolean>>({});
  const [topic, setTopic] = useState<string>("all");

  const add = useMutation({
    mutationFn: (id: string) => api.addMarketplaceWorkflow(id),
    onSuccess: (res) => {
      setAdded((a) => ({ ...a, [res.project_id]: true }));
      qc.invalidateQueries({ queryKey: ["workflow-projects"] });
    },
  });

  const [showAllTopics, setShowAllTopics] = useState(false);

  const workflows = data?.workflows ?? [];

  // A workflow can sit under several subjects now that the registry's
  // free-text topic is split server-side (#314), so the chip set is built from
  // the flattened tags with counts, most-populated first. Filters that match a
  // single workflow are real but not worth the width — they go behind "More",
  // which is most of them.
  const tagCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const w of workflows) {
      for (const t of topicsOf(w)) counts.set(t, (counts.get(t) ?? 0) + 1);
    }
    return [...counts.entries()].sort(
      (a, b) => b[1] - a[1] || a[0].localeCompare(b[0])
    );
  }, [workflows]);

  const primaryTags = tagCounts.filter(([, n]) => n > 1);
  const restTags = tagCounts.filter(([, n]) => n <= 1);
  const visibleTags = showAllTopics ? tagCounts : primaryTags;

  const shown =
    topic === "all"
      ? workflows
      : workflows.filter((w) => topicsOf(w).includes(topic));

  return (
    <div className="h-full overflow-auto">
      <div className="border-b border-line bg-surface px-6 py-4">
        <h1 className="text-lg font-semibold text-fg">Marketplace</h1>
        <p className="text-sm text-fgmuted">
          Ready-made Pegasus workflows from{" "}
          <a
            href="https://pegasushub.io/pages/workflows.html"
            target="_blank"
            rel="noreferrer"
            className="text-pegasus-600 hover:underline dark:text-pegasus-300"
          >
            PegasusHub
          </a>{" "}
          and Pegasus-ISI. Add one to clone it into your workspace, then
          Generate, Plan, and Run it from the Workflows page.
        </p>
      </div>

      <div className="p-6">
        {error && (
          <div className="mb-4 rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-rose-500/10 dark:text-rose-400">
            Failed to load the marketplace: {(error as Error).message}
          </div>
        )}

        {!isLoading && tagCounts.length > 1 && (
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <TopicChip
              label="All"
              count={workflows.length}
              active={topic === "all"}
              onClick={() => setTopic("all")}
            />
            {visibleTags.map(([t, n]) => (
              <TopicChip
                key={t}
                label={t}
                count={n}
                active={topic === t}
                onClick={() => setTopic(t)}
              />
            ))}
            {restTags.length > 0 && !showAllTopics && (
              <button
                onClick={() => setShowAllTopics(true)}
                className="rounded-full border border-dashed border-line px-3 py-1 text-xs font-medium text-fgmuted hover:bg-base"
              >
                More subjects
                <span className="ml-1 font-mono text-fgsubtle">
                  {restTags.length}
                </span>
              </button>
            )}
          </div>
        )}

        {isLoading ? (
          <p className="text-sm text-fgmuted">Loading workflows…</p>
        ) : shown.length === 0 ? (
          <p className="text-sm text-fgmuted">No workflows found.</p>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {shown.map((w) => (
              <WorkflowCard
                key={w.id}
                w={w}
                added={!!added[w.id]}
                pending={add.isPending && add.variables === w.id}
                onAdd={() => add.mutate(w.id)}
              />
            ))}
          </div>
        )}

        {add.isError && (
          <p className="mt-3 text-sm text-red-600 dark:text-rose-400">
            Couldn&apos;t add workflow: {(add.error as Error).message}
          </p>
        )}
      </div>
    </div>
  );
}

function WorkflowCard({
  w,
  added,
  pending,
  onAdd,
}: {
  w: MarketplaceWorkflow;
  added: boolean;
  pending: boolean;
  onAdd: () => void;
}) {
  const [imgOk, setImgOk] = useState(true);
  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-line bg-surface">
      {w.image && imgOk && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={w.image}
          alt=""
          onError={() => setImgOk(false)}
          className="h-32 w-full bg-base object-contain p-2"
        />
      )}
      <div className="flex flex-1 flex-col p-4">
        <div className="flex items-start justify-between gap-2">
          <h2 className="text-sm font-semibold text-fg">{w.name}</h2>
          <span
            title={`Source: ${w.source}`}
            className="shrink-0 rounded-full bg-base px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-fgmuted"
          >
            {w.source}
          </span>
        </div>
        {topicsOf(w).length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {topicsOf(w).map((t) => (
              <span
                key={t}
                className="w-fit rounded bg-pegasus-50 px-1.5 py-0.5 text-[10px] font-medium capitalize text-pegasus-700 dark:bg-pegasus-400/10 dark:text-pegasus-300"
              >
                {t}
              </span>
            ))}
          </div>
        )}
        <p className="mt-2 flex-1 text-xs leading-relaxed text-fgmuted">
          {w.description || "Pegasus workflow."}
        </p>
        <div className="mt-3 flex items-center gap-3">
          {added ? (
            <>
              <span className="text-xs font-medium text-green-600 dark:text-emerald-400">
                ✓ Added
              </span>
              <AppLink
                href="/workflows"
                className="text-xs font-medium text-pegasus-600 hover:underline dark:text-pegasus-300"
              >
                Open in Workflows →
              </AppLink>
            </>
          ) : (
            <button
              onClick={onAdd}
              disabled={pending}
              className="rounded-md bg-pegasus-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-pegasus-700 disabled:opacity-50"
            >
              {pending ? "Cloning…" : "Add to workspace"}
            </button>
          )}
          <a
            href={w.repo_url}
            target="_blank"
            rel="noreferrer"
            className="ml-auto text-xs font-medium text-fgmuted hover:text-fg hover:underline"
          >
            View on GitHub ↗
          </a>
        </div>
      </div>
    </div>
  );
}
