"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import * as api from "@/lib/api/client";
import { appRelativePath } from "@/lib/urls";

export default function ExamplesPage() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["examples"],
    queryFn: () => api.getExamples(),
  });
  // Track which example was just added so the card can show a confirmation.
  const [added, setAdded] = useState<Record<string, boolean>>({});

  const add = useMutation({
    mutationFn: (id: string) => api.addExample(id),
    onSuccess: (res) => {
      setAdded((a) => ({ ...a, [res.project_id]: true }));
      // The new project shows up on the Workflows page.
      qc.invalidateQueries({ queryKey: ["workflow-projects"] });
    },
  });

  const examples = data?.examples ?? [];

  return (
    <div className="h-full overflow-auto">
      <div className="border-b border-line bg-surface px-6 py-4">
        <h1 className="text-lg font-semibold text-fg">Example workflows</h1>
        <p className="text-sm text-fgmuted">
          Ready-made Pegasus workflows. Add one to your workspace, then
          Generate, Plan, and Run it from the Workflows page.
        </p>
      </div>

      <div className="p-6">
        {error && (
          <div className="mb-4 rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-rose-500/10 dark:text-rose-400">
            Failed to load examples: {(error as Error).message}
          </div>
        )}

        {isLoading ? (
          <p className="text-sm text-fgmuted">Loading examples…</p>
        ) : examples.length === 0 ? (
          <p className="text-sm text-fgmuted">
            No examples found. Check that the knowledge base includes example
            generators.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {examples.map((ex) => {
              const isAdded = added[ex.id];
              const pending = add.isPending && add.variables === ex.id;
              return (
                <div
                  key={ex.id}
                  className="flex flex-col rounded-lg border border-line bg-surface p-4"
                >
                  <h2 className="text-sm font-semibold text-fg">{ex.name}</h2>
                  <p className="mt-1 flex-1 text-xs leading-relaxed text-fgmuted">
                    {ex.description || "Pegasus workflow example."}
                  </p>
                  <div className="mt-3 flex items-center gap-3">
                    {isAdded ? (
                      <>
                        <span className="text-xs font-medium text-green-600 dark:text-emerald-400">
                          ✓ Added
                        </span>
                        <a
                          href={appRelativePath("/workflows")}
                          className="text-xs font-medium text-pegasus-600 hover:underline dark:text-pegasus-300"
                        >
                          Open in Workflows →
                        </a>
                      </>
                    ) : (
                      <button
                        onClick={() => add.mutate(ex.id)}
                        disabled={pending}
                        className="rounded-md bg-pegasus-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-pegasus-700 disabled:opacity-50"
                      >
                        {pending ? "Adding…" : "Add to workspace"}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {add.isError && (
          <p className="mt-3 text-sm text-red-600 dark:text-rose-400">
            Couldn&apos;t add example: {(add.error as Error).message}
          </p>
        )}
      </div>
    </div>
  );
}
