"use client";

import type { SkillMeta } from "@/lib/api/client";
import { useDashboard } from "@/lib/hooks/use-dashboard";
import { usePanelStore } from "@/lib/stores/panel-store";
import { appRelativePath, jupyterUrl } from "@/lib/urls";
import { cn, statusColor } from "@/lib/utils";

// Novice-friendly framing for the built-in AI skills: what task the skill
// does for you, keyed by slash command. Unknown skills fall back to their
// knowledge-layer name/description, so new skills appear automatically.
const SKILL_CARDS: Record<string, { icon: string; task: string }> = {
  "/scaffold": { icon: "🧱", task: "Create a new workflow" },
  "/wrapper": { icon: "📜", task: "Wrap a script as a workflow step" },
  "/dockerfile": { icon: "🐳", task: "Containerize your tools" },
  "/convert": { icon: "🔁", task: "Convert an existing pipeline" },
  "/review": { icon: "🔍", task: "Review a workflow for pitfalls" },
  "/debug": { icon: "🩺", task: "Debug a failed run" },
};

// Skills kept out of the dashboard showcase (still available in Chat):
// meta or infrastructure-heavy, not first-workflow tasks.
const HIDDEN_SKILLS = new Set(["/kiso", "/provd", "/help"]);

function SkillCard({
  skill,
  onOpenChat,
}: {
  skill: SkillMeta;
  onOpenChat: () => void;
}) {
  const friendly = skill.slash_command
    ? SKILL_CARDS[skill.slash_command]
    : undefined;
  return (
    <button
      onClick={onOpenChat}
      title={
        skill.slash_command
          ? `Opens PegasusAI Chat — type ${skill.slash_command} to start`
          : "Opens PegasusAI Chat"
      }
      className="group flex flex-col rounded-lg border border-line bg-surface p-4 text-left transition-colors hover:border-pegasus-300 hover:bg-base"
    >
      <div className="flex items-center gap-2">
        <span className="text-xl">{friendly?.icon ?? "✨"}</span>
        <span className="text-sm font-semibold text-fg">
          {friendly?.task ?? skill.name}
        </span>
      </div>
      <p className="mt-1.5 flex-1 text-xs leading-relaxed text-fgmuted">
        {skill.description}
      </p>
      {skill.slash_command && (
        <span className="mt-2 self-start rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-fgmuted group-hover:text-pegasus-700 dark:group-hover:text-pegasus-300">
          {skill.slash_command}
        </span>
      )}
    </button>
  );
}

function StatCard({
  label,
  value,
  detail,
  href,
}: {
  label: string;
  value: string | number;
  detail?: string;
  href?: string;
}) {
  const body = (
    <div className="rounded-lg border border-line bg-surface p-4 transition-colors hover:border-pegasus-300">
      <p className="text-xs font-medium uppercase tracking-wider text-fgsubtle">
        {label}
      </p>
      <p className="mt-1 text-2xl font-semibold text-fg">{value}</p>
      {detail && <p className="mt-1 text-xs text-fgmuted">{detail}</p>}
    </div>
  );
  return href ? <a href={href}>{body}</a> : body;
}

export default function DashboardPage() {
  const { data, isLoading, error } = useDashboard();
  const setChatOpen = usePanelStore((s) => s.setChatOpen);
  const openChat = () => setChatOpen(true);

  const skills = (data?.ai?.skills ?? []).filter(
    (s) => !s.slash_command || !HIDDEN_SKILLS.has(s.slash_command)
  );
  const personas = (data?.ai?.agents ?? []).filter((a) => a.id !== "general");
  const resources = data?.resources;
  const pool = resources?.pool;
  const poolUp = !!pool?.available;
  const runsByStatus = data?.runs.by_status ?? {};
  const runningCount = runsByStatus["running"] ?? 0;
  const failedCount = runsByStatus["failed"] ?? 0;

  return (
    <div className="h-full overflow-auto">
      <div className="border-b border-line bg-surface px-6 py-4">
        <h1 className="text-lg font-semibold text-fg">Dashboard</h1>
        <p className="text-sm text-fgmuted">
          Workflows, resources, and activity at a glance
        </p>
      </div>

      {error && (
        <div className="m-6 rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-rose-500/10 dark:text-rose-400">
          Failed to load dashboard: {(error as Error).message}
        </div>
      )}

      <div className="space-y-6 p-6">
        {/* Stat cards */}
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard
            label="Workflow projects"
            value={isLoading ? "…" : data?.projects.total ?? 0}
            detail={
              data
                ? Object.entries(data.projects.by_status)
                    .map(([s, n]) => `${n} ${s}`)
                    .join(" · ") || "none yet"
                : undefined
            }
            href={appRelativePath("/workflows")}
          />
          <StatCard
            label="Runs"
            value={isLoading ? "…" : data?.runs.total ?? 0}
            detail={
              data
                ? [
                    runningCount ? `${runningCount} running` : null,
                    failedCount ? `${failedCount} failed` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ") || "no active runs"
                : undefined
            }
            href={appRelativePath("/workflows")}
          />
          <StatCard
            label="Idle CPUs"
            value={
              isLoading ? "…" : poolUp ? `${pool?.idle_cpus ?? 0}/${pool?.total_cpus ?? 0}` : "—"
            }
            detail={
              poolUp
                ? `${pool?.machines ?? 0} machine(s)` +
                  ((pool?.total_gpus ?? 0) > 0
                    ? ` · ${pool?.idle_gpus ?? 0}/${pool?.total_gpus} GPUs idle`
                    : "")
                : "HTCondor pool not reachable"
            }
            href={appRelativePath("/provisioning")}
          />
          <StatCard
            label="Unread alerts"
            value={isLoading ? "…" : data?.notifications.unread ?? 0}
            detail={
              (resources?.pressure?.length ?? 0) > 0
                ? `${resources?.pressure.length} resource pressure finding(s)`
                : undefined
            }
          />
        </div>

        {/* Resource pressure banner */}
        {(resources?.pressure?.length ?? 0) > 0 && (
          <div className="rounded-md border border-amber-300 bg-amber-50 p-4 dark:border-amber-500/40 dark:bg-amber-500/10">
            <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">
              Resources required
            </p>
            <ul className="mt-1 space-y-1 text-sm text-amber-700 dark:text-amber-200">
              {resources?.pressure.map((f) => (
                <li key={f.kind}>
                  <span className="font-medium">{f.title}</span> — {f.detail}
                </li>
              ))}
            </ul>
            <a
              href={appRelativePath("/provisioning")}
              className="mt-2 inline-block text-sm font-medium text-amber-800 underline dark:text-amber-300"
            >
              View provisioning →
            </a>
          </div>
        )}

        {/* Built-in AI toolkit — capability cards for first-time users */}
        <section className="rounded-lg border border-line bg-surface">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold text-fg">
                What can Studio&apos;s AI help you do?
              </h2>
              <p className="text-xs text-fgmuted">
                Built-in Pegasus skills — click a card to open Chat, then type
                the command or just describe what you want.
              </p>
            </div>
            <button
              onClick={openChat}
              className="rounded-md bg-pegasus-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-pegasus-700"
            >
              💬 Open PegasusAI Chat
            </button>
          </div>

          <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 xl:grid-cols-3">
            {skills.map((skill) => (
              <SkillCard key={skill.name} skill={skill} onOpenChat={openChat} />
            ))}
            {/* Workbench is a capability of its own: bring your own AI tool */}
            <a
              href={appRelativePath("/workbench")}
              className="group flex flex-col rounded-lg border border-dashed border-line bg-surface p-4 transition-colors hover:border-pegasus-300 hover:bg-base"
            >
              <div className="flex items-center gap-2">
                <span className="text-xl">🛠️</span>
                <span className="text-sm font-semibold text-fg">
                  Bring your own AI tool
                </span>
              </div>
              <p className="mt-1.5 flex-1 text-xs leading-relaxed text-fgmuted">
                Install and launch Claude Code, Codex CLI, OpenCode, or
                Antigravity in the Workbench — preloaded with the same Pegasus
                knowledge.
              </p>
              <span className="mt-2 text-xs font-medium text-pegasus-600 dark:text-pegasus-300">
                Open Workbench →
              </span>
            </a>
          </div>

          {personas.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-2.5 text-xs text-fgmuted">
              <span>Chat personas:</span>
              {personas.map((a) => (
                <span
                  key={a.id}
                  title={a.description}
                  className="rounded-full bg-muted px-2 py-0.5 font-medium text-fg"
                >
                  {a.name}
                </span>
              ))}
              <span className="ml-auto">
                Not sure where to start? Open Chat and describe what you want.
              </span>
            </div>
          )}
        </section>

        <div className="grid gap-6 lg:grid-cols-2">
          {/* Recent runs */}
          <div className="rounded-lg border border-line bg-surface">
            <div className="flex items-center justify-between border-b border-line px-4 py-3">
              <h2 className="text-sm font-semibold text-fg">Recent runs</h2>
              <a
                href={appRelativePath("/workflows")}
                className="text-xs font-medium text-pegasus-600 hover:underline dark:text-pegasus-300"
              >
                All workflows →
              </a>
            </div>
            {isLoading ? (
              <p className="p-4 text-sm text-fgmuted">Scanning…</p>
            ) : (data?.runs.recent.length ?? 0) === 0 ? (
              <p className="p-4 text-sm text-fgmuted">
                No runs yet. Open Workflows to generate and submit one.
              </p>
            ) : (
              <ul className="divide-y divide-line">
                {data?.runs.recent.map((r) => (
                  <li key={r.run_id}>
                    <a
                      href={appRelativePath(
                        `/workflows?run=${encodeURIComponent(r.run_id)}`
                      )}
                      className="flex items-center gap-3 px-4 py-2.5 hover:bg-base"
                    >
                      <span
                        className={cn(
                          "rounded-full px-2 py-0.5 text-xs font-medium",
                          statusColor(r.status)
                        )}
                      >
                        {r.status}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-sm text-fg">
                        {r.name}
                        {r.run_name && (
                          <span className="text-fgsubtle"> / {r.run_name}</span>
                        )}
                      </span>
                      {r.total_jobs > 0 && (
                        <span className="text-xs text-fgmuted">
                          {r.completed_jobs}/{r.total_jobs} jobs
                          {r.failed_jobs > 0 && (
                            <span className="text-red-600 dark:text-rose-400">
                              {" "}
                              · {r.failed_jobs} failed
                            </span>
                          )}
                        </span>
                      )}
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Getting started / quick actions — the guided-tour entry point
              grows out of this card (TODO-PLAN item 20). */}
          <div className="rounded-lg border border-line bg-surface">
            <div className="border-b border-line px-4 py-3">
              <h2 className="text-sm font-semibold text-fg">Getting started</h2>
            </div>
            <ol className="space-y-3 p-4 text-sm text-fgmuted">
              <li className="flex gap-3">
                <span className="font-semibold text-pegasus-600 dark:text-pegasus-300">
                  1
                </span>
                <span>
                  Configure an LLM provider under{" "}
                  <a
                    href={appRelativePath("/settings")}
                    className="font-medium text-pegasus-600 hover:underline dark:text-pegasus-300"
                  >
                    Settings
                  </a>{" "}
                  to enable PegasusAI Chat and the AI Workbench.
                </span>
              </li>
              <li className="flex gap-3">
                <span className="font-semibold text-pegasus-600 dark:text-pegasus-300">
                  2
                </span>
                <span>
                  Open{" "}
                  <a
                    href={appRelativePath("/workflows")}
                    className="font-medium text-pegasus-600 hover:underline dark:text-pegasus-300"
                  >
                    Workflows
                  </a>{" "}
                  to generate, plan, and submit a Pegasus workflow.
                </span>
              </li>
              <li className="flex gap-3">
                <span className="font-semibold text-pegasus-600 dark:text-pegasus-300">
                  3
                </span>
                <span>
                  Watch pool capacity and demand under{" "}
                  <a
                    href={appRelativePath("/provisioning")}
                    className="font-medium text-pegasus-600 hover:underline dark:text-pegasus-300"
                  >
                    Provisioning
                  </a>
                  , or analyze data in{" "}
                  <a
                    href={jupyterUrl("/lab")}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-medium text-pegasus-600 hover:underline dark:text-pegasus-300"
                  >
                    JupyterLab ↗
                  </a>
                  .
                </span>
              </li>
            </ol>
          </div>
        </div>

        {/* Versions */}
        <div className="flex gap-4 text-xs text-fgsubtle">
          {data?.versions.pegasus && <span>{data.versions.pegasus}</span>}
          {data?.versions.condor && <span>{data.versions.condor}</span>}
        </div>
      </div>
    </div>
  );
}
