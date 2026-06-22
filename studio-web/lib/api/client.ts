/**
 * API client for studio-api backend.
 *
 * All requests go through /api/ which nginx (prod) or Next.js rewrites (dev)
 * proxies to the FastAPI backend on port 8080.
 */

import { apiUrl } from "@/lib/urls";

async function request<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const res = await fetch(apiUrl(path), {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status}: ${body}`);
  }
  return res.json();
}

// --- Health ---

export async function getHealth() {
  return request<{ status: string; version: string }>("/health");
}

export async function getDetailedHealth() {
  return request<{
    status: string;
    version: string;
    db_ok: boolean;
    pegasus_version: string | null;
    condor_version: string | null;
  }>("/health/detailed");
}

// --- Settings ---

export async function getSettings() {
  return request<{
    llm: LLMConfig | null;
    installed_tools: string[];
  }>("/settings");
}

// --- LLM ---

export interface LLMConfig {
  provider: string;
  model: string | null;
  api_key: string | null;
  base_url: string | null;
  extra_config: Record<string, unknown>;
  updated_at: string | null;
}

export interface LLMConfigRequest {
  provider: string;
  model?: string | null;
  api_key?: string | null;
  base_url?: string | null;
}

export interface ProviderInfo {
  id: string;
  name: string;
  base_url: string | null;
  default_model: string;
  api_key_env: string | null;
}

export async function getLLMConfig() {
  return request<LLMConfig>("/llm/config");
}

export async function updateLLMConfig(config: LLMConfigRequest) {
  return request<LLMConfig>("/llm/config", {
    method: "PUT",
    body: JSON.stringify(config),
  });
}

export async function getProviders() {
  return request<{ providers: ProviderInfo[] }>("/llm/providers");
}

export async function validateProvider(data: {
  provider: string;
  api_key: string;
  base_url?: string | null;
}) {
  return request<{ valid: boolean; models: string[]; error: string | null }>(
    "/llm/validate",
    { method: "POST", body: JSON.stringify(data) }
  );
}

// --- Provider Configs (multi-provider) ---

export interface ProviderConfig {
  provider_id: string;
  name: string;
  api_key: string;
  base_url: string;
  default_model: string;
  is_active: boolean;
  updated_at: string | null;
}

export interface ProviderConfigRequest {
  provider_id: string;
  name: string;
  api_key?: string;
  base_url?: string;
  default_model?: string;
  is_active?: boolean;
}

export async function getProviderConfigs() {
  return request<{ configs: ProviderConfig[] }>("/llm/provider-configs");
}

export async function upsertProviderConfig(config: ProviderConfigRequest) {
  return request<ProviderConfig>("/llm/provider-configs", {
    method: "PUT",
    body: JSON.stringify(config),
  });
}

export async function activateProvider(providerId: string) {
  return request<ProviderConfig>(
    `/llm/provider-configs/${providerId}/activate`,
    { method: "POST" }
  );
}

export async function deleteProviderConfig(providerId: string) {
  return request<{ status: string }>(`/llm/provider-configs/${providerId}`, {
    method: "DELETE",
  });
}

// --- Tools ---

export interface ToolInfo {
  id: string;
  name: string;
  vendor: string;
  description: string;
  type: "terminal" | "web";
  supports_mcp: boolean;
  supports_web: boolean;
  required_env: string[];
  paid?: boolean;
  icon: string | null;
  homepage: string | null;
}

export interface ToolDetail {
  info: ToolInfo;
  installed: boolean;
  status: string | null;
  process_pid: number | null;
  web_port: number | null;
}

export async function getTools() {
  return request<{ tools: ToolDetail[] }>("/tools");
}

export async function getTool(id: string) {
  return request<ToolDetail>(`/tools/${id}`);
}

export async function installTool(id: string) {
  return request<{ tool_id: string; status: string }>(`/tools/${id}/install`, {
    method: "POST",
  });
}

export async function uninstallTool(id: string) {
  return request<{ tool_id: string; status: string }>(
    `/tools/${id}/uninstall`,
    { method: "POST" }
  );
}

export async function startTool(id: string) {
  return request<{ status: string; pid?: number; port?: number }>(
    `/tools/${id}/start`,
    { method: "POST" }
  );
}

export async function stopTool(id: string) {
  return request<{ status: string }>(`/tools/${id}/stop`, { method: "POST" });
}

export async function getToolStatus(id: string) {
  return request<{ tool_id: string; running: boolean }>(`/tools/${id}/status`);
}

// --- Workflows ---

export interface WorkflowRun {
  run_id: string;
  run_name: string; // submit-dir name (e.g. "run0001") for display
  name: string;
  run_dir: string;
  status: string;
  total_jobs: number;
  completed_jobs: number;
  failed_jobs: number;
  exec_site: string | null;
  created_at: string;
  updated_at: string;
}

export async function getWorkflows() {
  return request<{ workflows: WorkflowRun[] }>("/workflows");
}

export async function getWorkflow(id: string) {
  return request<Record<string, unknown>>(`/workflows/${id}`);
}

export async function getWorkflowJobs(id: string) {
  return request<{ run_id: string; jobs: Record<string, unknown>[] }>(
    `/workflows/${id}/jobs`
  );
}

export async function analyzeWorkflow(id: string) {
  return request<{ run_id: string; analysis: string }>(
    `/workflows/${id}/analyze`,
    { method: "POST" }
  );
}

export async function deleteWorkflow(id: string) {
  return request<Record<string, unknown>>(`/workflows/${id}`, {
    method: "DELETE",
  });
}

export async function submitWorkflow(
  workflowDir: string,
  site = "condorpool",
  outputSite = "local"
) {
  return request<{ status: string; run_dir?: string; output?: string }>(
    `/workflows/submit?workflow_dir=${encodeURIComponent(workflowDir)}&site=${site}&output_site=${outputSite}`,
    { method: "POST" }
  );
}

// --- Workflow Projects ---

export interface WorkflowProjectRun {
  run_id: string;
  run_name: string; // submit-dir name (e.g. "run0001") for display
  name: string;
  run_dir: string;
  status: string;
}

export interface WorkflowProject {
  project_id: string;
  name: string;
  project_dir: string;
  status: string;
  has_generator: boolean;
  has_workflow_yml: boolean;
  has_dockerfile: boolean;
  runs: WorkflowProjectRun[];
}

export async function getWorkflowProjects() {
  return request<{ projects: WorkflowProject[] }>("/workflows/projects");
}

// Parameter schema discovered per workflow (different generators expose
// different arguments).
export interface WorkflowParam {
  dest: string;
  flag: string;
  flags?: string[];
  help: string;
  default: string | boolean | null;
  required: boolean;
  is_flag: boolean;
  choices: string[] | null;
}

export interface ProjectParams {
  project_id: string;
  generator: { params: WorkflowParam[]; mutex_required: string[][] };
  plan: { params: WorkflowParam[] };
}

export async function getProjectParams(projectId: string) {
  return request<ProjectParams>(
    `/workflows/projects/${encodeURIComponent(projectId)}/params`
  );
}

export async function generateWorkflow(projectId: string, args: string[] = []) {
  return request<{ status: string; output: string }>(
    `/workflows/projects/${encodeURIComponent(projectId)}/generate`,
    { method: "POST", body: JSON.stringify({ args }) }
  );
}

export interface PlanOptions {
  site?: string;
  output_site?: string;
}

export async function planWorkflow(projectId: string, opts: PlanOptions = {}) {
  return request<{ status: string; output: string }>(
    `/workflows/projects/${encodeURIComponent(projectId)}/plan`,
    { method: "POST", body: JSON.stringify(opts) }
  );
}

export async function submitWorkflowProject(
  projectId: string,
  opts: PlanOptions = {}
) {
  return request<{ project_id: string; status: string; run_dir?: string; output?: string }>(
    `/workflows/projects/${encodeURIComponent(projectId)}/submit`,
    { method: "POST", body: JSON.stringify(opts) }
  );
}

// --- Knowledge ---

export interface SkillMeta {
  name: string;
  description: string;
  slash_command: string | null;
}

export interface AgentInfo {
  id: string;
  name: string;
  description: string;
}

export async function getSkills() {
  return request<{ skills: SkillMeta[] }>("/knowledge/skills");
}

export async function getAgents() {
  return request<{ agents: AgentInfo[] }>("/knowledge/agents");
}

// --- Files ---

export interface FileEntry {
  name: string;
  type: string;
  size: number | null;
  modified: string | null;
}

export async function listFiles(path = "") {
  const q = path ? `?path=${encodeURIComponent(path)}` : "";
  return request<{ path: string; entries: FileEntry[] }>(`/files${q}`);
}

export async function readFile(path: string) {
  return request<{ path: string; content: string; size: number }>(
    `/files/read?path=${encodeURIComponent(path)}`
  );
}

export async function writeFile(path: string, content: string) {
  return request<{ path: string; size: number; status: string }>(
    "/files/write",
    { method: "POST", body: JSON.stringify({ path, content }) }
  );
}

// --- Chat ---

export interface ChatMessage {
  role: string;
  content: string;
  agent_id?: string | null;
  tool_calls?: Record<string, unknown> | null;
  created_at?: string | null;
}

export async function getChatHistory(limit = 100) {
  return request<{ messages: ChatMessage[] }>(`/chat/history?limit=${limit}`);
}

export async function stopChat(requestId?: string) {
  return request<{ status: string }>("/chat/stop", {
    method: "POST",
    body: JSON.stringify({ request_id: requestId }),
  });
}

// --- Jupyter ---

export async function startJupyter() {
  return request<{ status: string; port: number | null }>("/jupyter/start", {
    method: "POST",
  });
}

export async function stopJupyter() {
  return request<{ status: string }>("/jupyter/stop", { method: "POST" });
}

export async function getJupyterStatus() {
  return request<{ status: string; port: number | null }>("/jupyter/status");
}
