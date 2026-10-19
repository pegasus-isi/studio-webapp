"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import type { MarketplaceWorkflow } from "@/lib/api/client";
import * as api from "@/lib/api/client";
import { appRelativePath } from "@/lib/urls";
import { AppLink } from "@/components/layout/app-link";
import { useWorkflowProjects } from "@/lib/hooks/use-workflows";

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
  const [addError, setAddError] = useState<Record<string, string>>({});
  const [topic, setTopic] = useState<string>("all");

  // Projects already in the workspace, by folder name — the same name "Add"
  // clones into. Without this the card offered "Add to workspace" for a
  // workflow you already had, and the 409 that came back was printed below
  // the whole grid, off-screen, so the click looked like it did nothing.
  const { data: projectData } = useWorkflowProjects();
  const inWorkspace = useMemo(
    () => new Set((projectData?.projects ?? []).map((p) => p.project_id)),
    [projectData]
  );

  const add = useMutation({
    mutationFn: (id: string) => api.addMarketplaceWorkflow(id),
    onMutate: (id) => {
      setAddError(({ [id]: _cleared, ...rest }) => rest);
    },
    onSuccess: (res) => {
      setAdded((a) => ({ ...a, [res.project_id]: true }));
      qc.invalidateQueries({ queryKey: ["workflow-projects"] });
    },
    onError: (e, id) => {
      setAddError((m) => ({ ...m, [id]: (e as Error).message }));
      // A 409 means the folder appeared since the list was fetched (another
      // tab, a terminal clone); refetch so the card switches to "Open".
      qc.invalidateQueries({ queryKey: ["workflow-projects"] });
    },
  });

  const [showAllTopics, setShowAllTopics] = useState(false);
  const [query, setQuery] = useState("");
  const [source, setSource] = useState<"all" | "Pegasus-ISI" | "PegasusHub">(
    "all"
  );
  const [sort, setSort] = useState<"featured" | "name">("featured");

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

  // Four filters that narrow together: free text, subject, where it came from,
  // and ordering. Twenty entries was already too many to scan when nine of the
  // subject chips matched a single workflow each (#314).
  const needle = query.trim().toLowerCase();
  const shown = useMemo(() => {
    const out = workflows.filter((w) => {
      if (topic !== "all" && !topicsOf(w).includes(topic)) return false;
      if (source !== "all" && w.source !== source) return false;
      if (
        needle &&
        !`${w.name} ${w.description} ${topicsOf(w).join(" ")}`
          .toLowerCase()
          .includes(needle)
      )
        return false;
      return true;
    });
    // The API already returns featured-first, so only re-sort when asked —
    // otherwise the gentle-onramp ordering is lost for no reason.
    return sort === "name"
      ? [...out].sort((a, b) => a.name.localeCompare(b.name))
      : out;
  }, [workflows, topic, source, needle, sort]);

  const filtering = topic !== "all" || source !== "all" || !!needle;
  const clearAll = () => {
    setTopic("all");
    setSource("all");
    setQuery("");
  };

  return (
    <div className="h-full overflow-auto">
      <div className="border-b border-line bg-surface px-6 py-4">
        {/* "Examples", as the rail, ⌘K and the tour call it; the route and
            the API keep their old "marketplace" name. */}
        <h1 className="text-lg font-semibold text-fg">Examples</h1>
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
          and Pegasus-ISI. Add one to copy it into your workspace, then
          open it in Analyses to run it.
        </p>
      </div>

      <div className="p-6">
        {error && (
          <div className="mb-4 rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-rose-500/10 dark:text-rose-400">
            Failed to load the marketplace: {(error as Error).message}
          </div>
        )}

        {!isLoading && workflows.length > 0 && (
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <label className="relative min-w-[200px] flex-1 sm:max-w-xs">
              <span className="sr-only">Search examples</span>
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                aria-hidden="true"
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-fgsubtle"
              >
                <circle cx="11" cy="11" r="7" />
                <path d="M20 20l-3.5-3.5" />
              </svg>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search by name, description, or subject"
                className="h-8 w-full rounded-lg border border-line bg-surface pl-8 pr-2.5 text-xs text-fg outline-none placeholder:text-fgsubtle focus:border-pegasus-400"
              />
            </label>

            <label className="flex items-center gap-1.5 text-xs text-fgmuted">
              <span className="sr-only">Filter by source</span>
              <select
                value={source}
                onChange={(e) =>
                  setSource(e.target.value as typeof source)
                }
                className="h-8 rounded-lg border border-line bg-surface px-2 text-xs text-fg outline-none focus:border-pegasus-400"
              >
                <option value="all">Any source</option>
                <option value="Pegasus-ISI">Pegasus-ISI</option>
                <option value="PegasusHub">PegasusHub</option>
              </select>
            </label>

            <label className="flex items-center gap-1.5 text-xs text-fgmuted">
              <span className="sr-only">Sort</span>
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value as typeof sort)}
                className="h-8 rounded-lg border border-line bg-surface px-2 text-xs text-fg outline-none focus:border-pegasus-400"
              >
                <option value="featured">Suggested first</option>
                <option value="name">Name A–Z</option>
              </select>
            </label>

            <span className="ml-auto text-xs text-fgsubtle">
              {shown.length} of {workflows.length}
            </span>
            {filtering && (
              <button
                onClick={clearAll}
                className="rounded-lg border border-line px-2 py-1 text-xs font-medium text-fgmuted hover:text-fg"
              >
                Clear filters
              </button>
            )}
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
          <div className="rounded-lg border border-dashed border-line p-8 text-center">
            <p className="text-sm text-fgmuted">
              Nothing matches those filters.
            </p>
            {filtering && (
              <button
                onClick={clearAll}
                className="mt-2 text-xs font-semibold text-pegasus-700 hover:underline dark:text-pegasus-300"
              >
                Clear filters
              </button>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {shown.map((w) => (
              <WorkflowCard
                key={w.id}
                w={w}
                added={!!added[w.id]}
                existing={inWorkspace.has(w.id)}
                error={addError[w.id]}
                pending={add.isPending && add.variables === w.id}
                onAdd={() => add.mutate(w.id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function WorkflowCard({
  w,
  added,
  existing,
  error,
  pending,
  onAdd,
}: {
  w: MarketplaceWorkflow;
  added: boolean;
  /** A folder of this name is already in the workspace. */
  existing: boolean;
  /** Why the last Add of this card failed, shown on the card itself. */
  error?: string;
  pending: boolean;
  onAdd: () => void;
}) {
  const openHref = `/workflows?project=${encodeURIComponent(w.id)}`;
  // No thumbnail. The registry's `image` is a rendering of the workflow's DAG,
  // which at 128px tall is an unreadable smudge that says only "this is a
  // graph" — true of every entry — while taking the space the description
  // needed. It stays in the API for a Preview drawer to use at a size where a
  // graph is actually legible (#314).
  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-line bg-surface">
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
        {/* No placeholder. A generic sentence repeated down the grid looks
            like content and carries none — blank is more honest, and the
            server now fills most of these from the repo's own metadata. */}
        {w.description ? (
          <p className="mt-2 flex-1 text-xs leading-relaxed text-fgmuted">
            {w.description}
          </p>
        ) : (
          <div className="flex-1" />
        )}
        <div className="mt-3 flex items-center gap-3">
          {added || existing ? (
            <>
              <span className="text-xs font-medium text-emerald-700 dark:text-emerald-400">
                {added ? "✓ Added" : "✓ In your workspace"}
              </span>
              <AppLink
                href={openHref}
                className="text-xs font-medium text-pegasus-600 hover:underline dark:text-pegasus-300"
              >
                Open →
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
        {error && !existing && (
          <p role="alert" className="mt-2 text-xs text-red-600 dark:text-rose-400">
            Couldn&apos;t add: {error}
          </p>
        )}
      </div>
    </div>
  );
}
