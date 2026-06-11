"use client";

import { useEffect, useState } from "react";
import { usePanelStore } from "@/lib/stores/panel-store";
import { useToolStore } from "@/lib/stores/tool-store";
import { useTools, useStartTool } from "@/lib/hooks/use-tools";
import { ToolCard } from "@/components/tools/tool-card";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api/client";

export default function WorkbenchPage() {
  const setTerminalOpen = usePanelStore((s) => s.setTerminalOpen);
  const terminalOpen = usePanelStore((s) => s.terminalOpen);
  const tabs = useToolStore((s) => s.tabs);
  const addTab = useToolStore((s) => s.addTab);
  const { data: toolsData, isLoading: toolsLoading } = useTools();
  const startTool = useStartTool();

  const [skills, setSkills] = useState<api.SkillMeta[]>([]);
  const [agents, setAgents] = useState<api.AgentInfo[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(true);

  // Load skills and agents
  useEffect(() => {
    Promise.all([
      api.getSkills().catch(() => ({ skills: [] })),
      api.getAgents().catch(() => ({ agents: [] })),
    ]).then(([skillsRes, agentsRes]) => {
      setSkills(skillsRes.skills);
      setAgents(agentsRes.agents);
      setSkillsLoading(false);
    });
  }, []);

  const tools = toolsData?.tools ?? [];
  const installedTools = tools.filter((t) => t.installed);

  const handleOpenBash = () => {
    addTab(null, "Terminal");
    setTerminalOpen(true);
  };

  const handleLaunchTool = async (toolId: string, toolName: string) => {
    await startTool.mutateAsync(toolId);
    addTab(toolId, toolName);
    setTerminalOpen(true);
  };

  return (
    <div className="flex h-full flex-col">
      {/* Header bar */}
      <div className="border-b border-gray-200 bg-white px-6 py-3">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">AI Workbench</h1>
          <p className="text-sm text-gray-500">
            Launch terminals and AI coding tools for workflow development.
          </p>
        </div>
      </div>

      {/* Content area */}
      <div className="flex-1 overflow-auto bg-gray-50 p-6">
        <div className="mx-auto max-w-5xl space-y-6">
          {/* Terminal panel notice */}
          {terminalOpen && tabs.length > 0 && (
            <div className="flex items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2.5">
              <span className="inline-block h-2.5 w-2.5 rounded-full bg-blue-500" />
              <span className="text-sm text-blue-800">
                {tabs.length} terminal session{tabs.length !== 1 ? "s" : ""}{" "}
                active in the panel below.
              </span>
            </div>
          )}

          {/* Quick Launch */}
          <section>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-500">
              Quick Launch
            </h2>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {/* Bash Terminal card */}
              <button
                onClick={handleOpenBash}
                className="group flex flex-col rounded-lg border border-gray-200 bg-white p-4 text-left transition-colors hover:border-gray-400"
              >
                <div className="flex items-center gap-3">
                  <div className="flex h-9 w-9 items-center justify-center rounded-md bg-gray-800 text-white">
                    <svg
                      width="18"
                      height="18"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <polyline points="4 17 10 11 4 5" />
                      <line x1="12" y1="19" x2="20" y2="19" />
                    </svg>
                  </div>
                  <div>
                    <h3 className="text-sm font-medium text-gray-900">
                      Bash Terminal
                    </h3>
                    <p className="text-xs text-gray-500">
                      Open a new shell session
                    </p>
                  </div>
                </div>
              </button>

              {/* Installed tool cards as quick launchers */}
              {installedTools
                .filter((t) => t.info.type === "terminal")
                .map((tool) => (
                  <button
                    key={tool.info.id}
                    onClick={() =>
                      tool.status === "running"
                        ? (() => {
                            addTab(tool.info.id, tool.info.name);
                            setTerminalOpen(true);
                          })()
                        : handleLaunchTool(tool.info.id, tool.info.name)
                    }
                    disabled={startTool.isPending}
                    className="group flex flex-col rounded-lg border border-gray-200 bg-white p-4 text-left transition-colors hover:border-gray-400 disabled:opacity-50"
                  >
                    <div className="flex items-center gap-3">
                      <div className="flex h-9 w-9 items-center justify-center rounded-md bg-pegasus-100 text-pegasus-700">
                        <svg
                          width="18"
                          height="18"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <path d="M12 2L2 7l10 5 10-5-10-5z" />
                          <path d="M2 17l10 5 10-5" />
                          <path d="M2 12l10 5 10-5" />
                        </svg>
                      </div>
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <h3 className="text-sm font-medium text-gray-900">
                            {tool.info.name}
                          </h3>
                          {tool.status === "running" && (
                            <span className="rounded-full bg-green-100 px-1.5 py-0.5 text-[10px] font-medium text-green-700">
                              Running
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-gray-500">
                          {tool.info.vendor}
                        </p>
                      </div>
                    </div>
                  </button>
                ))}
            </div>
          </section>

          {/* All Tools */}
          {tools.length > 0 && (
            <section>
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-500">
                AI Tools
              </h2>
              {toolsLoading ? (
                <p className="text-sm text-gray-400">Loading tools...</p>
              ) : (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {tools.map((tool) => (
                    <ToolCard key={tool.info.id} tool={tool} />
                  ))}
                </div>
              )}
            </section>
          )}

          {/* Skills & Agents */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            {/* Skills */}
            <section>
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-500">
                Pegasus Skills
              </h2>
              {skillsLoading ? (
                <p className="text-sm text-gray-400">Loading skills...</p>
              ) : skills.length === 0 ? (
                <div className="rounded-lg border border-dashed border-gray-300 bg-white p-4">
                  <p className="text-sm text-gray-500">
                    No skills loaded. Check that the{" "}
                    <code className="rounded bg-gray-100 px-1 py-0.5 text-xs">
                      knowledge/skills/
                    </code>{" "}
                    directory contains skill definitions with{" "}
                    <code className="rounded bg-gray-100 px-1 py-0.5 text-xs">
                      metadata.json
                    </code>{" "}
                    files.
                  </p>
                </div>
              ) : (
                <div className="space-y-2">
                  {skills.map((skill) => (
                    <div
                      key={skill.name}
                      className="flex items-center justify-between rounded-lg border border-gray-200 bg-white px-4 py-2.5"
                    >
                      <div>
                        <span className="text-sm font-medium text-gray-900">
                          {skill.name}
                        </span>
                        <p className="text-xs text-gray-500 line-clamp-1">
                          {skill.description}
                        </p>
                      </div>
                      {skill.slash_command && (
                        <code className="shrink-0 rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
                          {skill.slash_command}
                        </code>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* Agents */}
            <section>
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-500">
                Agent Personas
              </h2>
              {skillsLoading ? (
                <p className="text-sm text-gray-400">Loading agents...</p>
              ) : agents.length === 0 ? (
                <div className="rounded-lg border border-dashed border-gray-300 bg-white p-4">
                  <p className="text-sm text-gray-500">
                    No agents loaded. Check that the{" "}
                    <code className="rounded bg-gray-100 px-1 py-0.5 text-xs">
                      knowledge/agents/
                    </code>{" "}
                    directory contains agent markdown files with YAML
                    frontmatter.
                  </p>
                </div>
              ) : (
                <div className="space-y-2">
                  {agents.map((agent) => (
                    <div
                      key={agent.id}
                      className="flex items-center justify-between rounded-lg border border-gray-200 bg-white px-4 py-2.5"
                    >
                      <div>
                        <span className="text-sm font-medium text-gray-900">
                          {agent.name}
                        </span>
                        <p className="text-xs text-gray-500 line-clamp-1">
                          {agent.description}
                        </p>
                      </div>
                      <span className="shrink-0 rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-gray-600">
                        {agent.id}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
