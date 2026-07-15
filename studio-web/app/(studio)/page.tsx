"use client";

import type { ReactNode } from "react";
import type { SkillMeta } from "@/lib/api/client";
import { useDashboard } from "@/lib/hooks/use-dashboard";
import { usePanelStore } from "@/lib/stores/panel-store";
import { appRelativePath, jupyterUrl } from "@/lib/urls";
import { cn, statusColor } from "@/lib/utils";

// Slash commands not shown as capability cards (meta / infra-heavy — still
// available in Chat). New skills appear automatically otherwise.
const HIDDEN_SKILLS = new Set(["/kiso", "/provd", "/help"]);

// Novice-facing framing per skill: an icon and a short task label so each
// capability reads as a concrete thing you can do. Unknown skills fall back
// to their knowledge-layer name.
const SKILL_META: Record<string, { icon: string; task: string }> = {
  "/scaffold": { icon: "🧱", task: "Create a new workflow" },
  "/wrapper": { icon: "📜", task: "Wrap a script as a step" },
  "/dockerfile": { icon: "🐳", task: "Containerize tools" },
  "/convert": { icon: "🔁", task: "Convert a pipeline" },
  "/review": { icon: "🔍", task: "Review a workflow" },
  "/debug": { icon: "🩺", task: "Debug a failed run" },
};

// Compact AI-capability card: icon + task + full description. Clicking opens
// Chat and pre-fills the skill's slash command so the user just adds details.
function CapabilityCard({
  skill,
  onStart,
}: {
  skill: SkillMeta;
  onStart: (skill: SkillMeta) => void;
}) {
  const meta = skill.slash_command ? SKILL_META[skill.slash_command] : undefined;
  return (
    <button
      onClick={() => onStart(skill)}
      title={
        skill.slash_command
          ? `Opens Chat pre-filled with ${skill.slash_command} — add your details and send`
          : "Opens Chat"
      }
      className="flex items-start gap-3 rounded-lg border border-line bg-surface p-3 text-left transition-colors hover:border-pegasus-400 hover:bg-base"
    >
      <span className="text-xl leading-none">{meta?.icon ?? "✨"}</span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-semibold text-fg">
          {meta?.task ?? skill.name}
        </span>
        <span className="mt-0.5 block text-xs leading-relaxed text-fgmuted">
          {skill.description}
        </span>
        {skill.slash_command && (
          <span className="mt-1.5 inline-block rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-fgsubtle">
            {skill.slash_command}
          </span>
        )}
      </span>
    </button>
  );
}

// One big "what do you want to do?" card. Each maps to a distinct area so
// every click lands somewhere useful; the description carries the capability.
function ActionCard({
  icon,
  title,
  description,
  onClick,
  href,
  external,
}: {
  icon: string;
  title: string;
  description: string;
  onClick?: () => void;
  href?: string;
  external?: boolean;
}) {
  const inner = (
    <>
      <span className="text-2xl">{icon}</span>
      <span className="mt-2 block text-base font-semibold text-fg">
        {title}
      </span>
      <span className="mt-1 block text-sm leading-relaxed text-fgmuted">
        {description}
      </span>
    </>
  );
  const className =
    "flex h-full flex-col rounded-xl border border-line bg-surface p-5 text-left transition-colors hover:border-pegasus-400 hover:bg-base";

  if (href) {
    return (
      <a
        href={href}
        {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
        className={className}
      >
        {inner}
      </a>
    );
  }
  return (
    <button onClick={onClick} className={className}>
      {inner}
    </button>
  );
}

export default function DashboardPage() {
  const { data, isLoading } = useDashboard();
  const setChatOpen = usePanelStore((s) => s.setChatOpen);
  const openChatWith = usePanelStore((s) => s.openChatWith);
  const openChat = () => setChatOpen(true);

  // Card click → open chat with the skill's slash command pre-filled (trailing
  // space so the user types their details right after). No command → plain open.
  const startSkill = (skill: SkillMeta) =>
    openChatWith(skill.slash_command ? `${skill.slash_command} ` : "");

  const skills = (data?.ai?.skills ?? []).filter(
    (s) => !s.slash_command || !HIDDEN_SKILLS.has(s.slash_command)
  );
  const pool = data?.resources?.pool;
  const poolUp = !!pool?.available;
  const pressure = data?.resources?.pressure ?? [];
  const running = data?.runs.by_status?.["running"] ?? 0;
  const unread = data?.notifications.unread ?? 0;

  // Compact one-line status: only include facts we actually have.
  const glance: ReactNode[] = [];
  if (data) {
    glance.push(<span key="p">{data.projects.total} projects</span>);
    glance.push(<span key="r">{data.runs.total} runs</span>);
    if (running > 0)
      glance.push(
        <span key="run" className="text-blue-600 dark:text-blue-300">
          {running} running
        </span>
      );
    if (poolUp)
      glance.push(
        <span key="c">
          {pool?.idle_cpus ?? 0}/{pool?.total_cpus ?? 0} CPUs idle
        </span>
      );
    if (unread > 0) glance.push(<span key="u">{unread} unread</span>);
  }

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto max-w-5xl px-6 py-8">
        {/* Welcome */}
        <h1 className="text-2xl font-semibold text-fg">
          Welcome to PegasusAI Studio
        </h1>
        <p className="mt-1 text-sm text-fgmuted">
          Build, run, and monitor Pegasus workflows — with AI, in one
          workspace.
        </p>

        {/* Build with AI — one card per capability so each is discoverable */}
        {skills.length > 0 && (
          <>
            <div className="mt-8 flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-medium uppercase tracking-wider text-fgsubtle">
                Build a workflow with AI
              </h2>
              <button
                onClick={openChat}
                className="rounded-md bg-pegasus-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-pegasus-700"
              >
                💬 Open Chat
              </button>
            </div>
            <p className="mt-1 text-sm text-fgmuted">
              Pick a task or just describe what you need — the AI does the rest.
            </p>
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {skills.map((s) => (
                <CapabilityCard key={s.name} skill={s} onStart={startSkill} />
              ))}
            </div>
          </>
        )}

        {/* Work with your workflows */}
        <h2 className="mt-8 text-sm font-medium uppercase tracking-wider text-fgsubtle">
          Work with your workflows
        </h2>
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <ActionCard
            icon="📊"
            title="Run & monitor"
            description="Generate, plan, build the image, submit, and watch jobs, logs, and status."
            href={appRelativePath("/workflows")}
          />
          <ActionCard
            icon="🖥️"
            title="Resources"
            description="See pool capacity, workflow demand, and alerts when more resources are needed."
            href={appRelativePath("/provisioning")}
          />
          <ActionCard
            icon="📓"
            title="Notebooks"
            description="Open JupyterLab in the same workspace to analyze results and iterate."
            href={jupyterUrl("/lab")}
            external
          />
        </div>

        {/* At a glance — one muted line */}
        <div className="mt-8 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-fgmuted">
          <span className="font-medium text-fgsubtle">At a glance:</span>
          {isLoading ? (
            <span>loading…</span>
          ) : glance.length ? (
            glance.map((g, i) => (
              <span key={i} className="flex items-center gap-2">
                {i > 0 && <span className="text-fgsubtle">·</span>}
                {g}
              </span>
            ))
          ) : (
            <span>no activity yet</span>
          )}
        </div>

        {/* Resource pressure — only when something needs attention */}
        {pressure.length > 0 && (
          <a
            href={appRelativePath("/provisioning")}
            className="mt-3 flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 hover:bg-amber-100 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-300"
          >
            <span>⚠</span>
            <span>
              {pressure[0].title}
              {pressure.length > 1 && ` +${pressure.length - 1} more`} — view
              provisioning
            </span>
          </a>
        )}

        {/* Recent runs — a short, light list */}
        {(data?.runs.recent.length ?? 0) > 0 && (
          <div className="mt-8">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-sm font-medium uppercase tracking-wider text-fgsubtle">
                Recent runs
              </h2>
              <a
                href={appRelativePath("/workflows")}
                className="text-xs font-medium text-pegasus-600 hover:underline dark:text-pegasus-300"
              >
                All workflows →
              </a>
            </div>
            <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
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
                      </span>
                    )}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
