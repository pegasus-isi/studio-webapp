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
    // FastAPI errors are {"detail": "..."} — surface the detail so callers can
    // show the real message (e.g. an npm EACCES install failure) rather than a
    // raw "500: {...}" blob.
    let message = body;
    try {
      const parsed = JSON.parse(body);
      if (parsed && typeof parsed.detail === "string") message = parsed.detail;
    } catch {
      /* body wasn't JSON — keep the raw text */
    }
    throw new Error(message || `Request failed (${res.status})`);
  }
  return res.json();
}

// --- Health ---

export async function getHealth() {
  // channel is a pre-release qualifier ("alpha"); older backends omit it.
  return request<{ status: string; version: string; channel?: string }>(
    "/health"
  );
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

// --- Container registry (image Build action) ---

export interface RegistrySettings {
  registry: string;
  username: string;
  has_password: boolean;
}

export async function getRegistrySettings() {
  return request<RegistrySettings>("/settings/registry");
}

export async function updateRegistrySettings(data: {
  registry: string;
  username: string;
  password?: string; // omit to keep the stored password; "" clears it
}) {
  return request<RegistrySettings>("/settings/registry", {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

// --- Workflow discovery (extra scan roots / exclude fragments, #306) ---

export interface WorkflowScanSettings {
  /** User-added directories scanned for runs, merged with the env default. */
  roots: string[];
  /** User-added exclude fragments, merged with the builtin list. */
  excludes: string[];
  /** Roots that don't exist right now (unmounted volume, typo) — scanned
      anyway once they appear, but worth surfacing. */
  missing_roots: string[];
  defaults: {
    scan_root: string;
    workflows_dir: string;
    builtin_excludes: string[];
  };
}

export async function getWorkflowScanSettings() {
  return request<WorkflowScanSettings>("/settings/workflow-scan");
}

export async function updateWorkflowScanSettings(data: {
  roots: string[];
  excludes: string[];
}) {
  return request<WorkflowScanSettings>("/settings/workflow-scan", {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

// --- LLM ---

export interface LLMConfig {
  provider: string;
  model: string | null;
  // Keys are write-only server-side: GETs only report whether one is saved.
  has_api_key: boolean;
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
  // Write-only key, as in LLMConfig.
  has_api_key: boolean;
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

// Models a saved provider offers, resolved server-side with its STORED key
// (the browser never sees saved keys).
export async function getSavedProviderModels(providerId: string) {
  return request<{ valid: boolean; models: string[]; error: string | null }>(
    `/llm/provider-configs/${providerId}/models`
  );
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

/** Take a run out of the Condor queue, leaving its directory (and logs) intact. */
export async function cancelWorkflow(id: string) {
  return request<{ run_id: string; status: string; output?: string }>(
    `/workflows/${id}/cancel`,
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
  const qs = new URLSearchParams({
    workflow_dir: workflowDir,
    site,
    output_site: outputSite,
  });
  return request<{ status: string; run_dir?: string; output?: string }>(
    `/workflows/submit?${qs}`,
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
  /** Path relative to $HOME (JupyterLab's root); "" when outside it. */
  jupyter_path: string;
}

export interface WorkflowProject {
  project_id: string;
  name: string;
  project_dir: string;
  status: string;
  /** Path relative to $HOME (JupyterLab's root); "" when outside it. */
  jupyter_path: string;
  has_generator: boolean;
  has_workflow_yml: boolean;
  has_container_def: boolean;
  runs: WorkflowProjectRun[];
}

export async function getWorkflowProjects() {
  return request<{ projects: WorkflowProject[] }>("/workflows/projects");
}

// Parameter schema discovered per workflow (different generators expose
// different arguments).
/**
 * Widget hint from argparse introspection (#241, Phase 3).
 *
 * A hint, not a contract: every value is still a string on the command line.
 * Anything unrecognised must render as a plain text input.
 */
export type ParamType =
  | "text"
  | "integer"
  | "number"
  | "boolean"
  | "choice"
  | "file"
  | "directory"
  | "date";

export interface WorkflowParam {
  dest: string;
  flag: string;
  flags?: string[];
  help: string;
  default: string | boolean | null;
  required: boolean;
  is_flag: boolean;
  choices: string[] | null;
  // Phase 3 hints. Optional so a backend that predates them (or an
  // introspection fallback that returns a bare schema) still renders.
  type?: ParamType;
  /** Human label, preferring the author's help text over the flag name. */
  label?: string;
  /** Plumbing rather than science — belongs under "More options". */
  advanced?: boolean;
}

export interface ProjectParams {
  project_id: string;
  generator: { params: WorkflowParam[]; mutex_required: string[][] };
  plan: { params: WorkflowParam[] };
}

export async function deleteWorkflowProject(projectId: string) {
  return request<{ project_id: string; status: string }>(
    `/workflows/projects/${encodeURIComponent(projectId)}`,
    { method: "DELETE" }
  );
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
  // Run action only: the planned run dir (from a prior Plan) to submit.
  run_dir?: string;
}

export async function planWorkflow(projectId: string, opts: PlanOptions = {}) {
  return request<{ status: string; output: string; run_dir?: string }>(
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

// --- Unified Run flow (#241, Phase 1) ---
//
// One Run action replacing the Generate / Build / Plan / Run button row. The
// flow runs server-side and outlives the request that starts it, so the three
// calls here are start, reattach, and (via EventSource) follow.

export type RunFlowStageStatus =
  | "pending"
  | "running"
  | "done"
  | "skipped"
  | "failed";

export interface RunFlowStage {
  key: string;
  /** Already in the user's terms — render as-is, don't re-map. */
  label: string;
  status: RunFlowStageStatus;
  detail: string;
}

export interface RunFlowFailure {
  stage: string;
  /** What went wrong, in plain language. */
  cause: string;
  /** What to do about it. Never empty. */
  action: string;
  /** Raw log — collapsed by default, never discarded. */
  technical: string;
}

export interface RunFlowSnapshot {
  flow_id?: string;
  project_id: string;
  state: "running" | "succeeded" | "failed" | null;
  stages?: RunFlowStage[];
  failure?: RunFlowFailure | null;
  run_dir?: string;
  active: boolean;
}

export interface RunFlowOptions {
  args?: string[];
  site?: string;
  output_site?: string;
  force_build?: boolean;
}

export async function startRunFlow(
  projectId: string,
  opts: RunFlowOptions = {}
) {
  return request<RunFlowSnapshot>(
    `/workflows/projects/${encodeURIComponent(projectId)}/run-flow`,
    { method: "POST", body: JSON.stringify(opts) }
  );
}

export async function getRunFlow(projectId: string) {
  return request<RunFlowSnapshot>(
    `/workflows/projects/${encodeURIComponent(projectId)}/run-flow`
  );
}

// --- UI preferences (per-user rollout flag) ---

export interface UIPreferences {
  /**
   * The simplified interface: one Run action, and a sidebar that leads with
   * what a scientist does rather than what the infrastructure offers. On by
   * default; turning it off restores the four Pegasus verbs and the full nav.
   */
  simple_run: boolean;
  /** Run form's Advanced disclosure (the individual verbs). */
  advanced_open: boolean;
  welcome_dismissed: boolean;
  /** Sidebar's Advanced group (Resources, Terminal). */
  nav_advanced_open: boolean;
}

export async function getUIPreferences() {
  return request<UIPreferences>("/settings/ui");
}

export async function updateUIPreferences(prefs: UIPreferences) {
  return request<UIPreferences>("/settings/ui", {
    method: "PUT",
    body: JSON.stringify(prefs),
  });
}

// --- Run outputs (#241, Phase 4) ---

export interface OutputFile {
  /** Path relative to the run's output directory. */
  path: string;
  name: string;
  size: number;
  modified: string;
  kind: "image" | "table" | "text" | "binary";
  /** Decided server-side, so the size threshold can't drift between the two. */
  previewable: boolean;
}

export interface RunOutputs {
  run_id: string;
  output_dir: string | null;
  /**
   * How the output directory was located. "catalog" = the workflow declared
   * it, so an empty listing means "nothing produced yet". "convention" = we
   * guessed from a directory name. "none" = we couldn't tell where this
   * workflow puts results, which is NOT the same as there being none.
   */
  found_via: "catalog" | "convention" | "none";
  exists: boolean;
  total: number;
  files: OutputFile[];
  /** The walk hit its ceiling — more files exist than are listed. */
  truncated: boolean;
  offset: number;
  limit: number;
}

export async function getRunOutputs(
  runId: string,
  opts: { offset?: number; limit?: number } = {}
) {
  const query = new URLSearchParams();
  if (opts.offset) query.set("offset", String(opts.offset));
  if (opts.limit) query.set("limit", String(opts.limit));
  const suffix = query.toString() ? `?${query}` : "";
  return request<RunOutputs>(
    `/workflows/${encodeURIComponent(runId)}/outputs${suffix}`
  );
}

/** URL for previewing or downloading one output file. */
export function runOutputFileUrl(
  runId: string,
  path: string,
  download = false
): string {
  const query = new URLSearchParams({ path });
  if (download) query.set("download", "1");
  return apiUrl(
    `workflows/${encodeURIComponent(runId)}/outputs/file?${query}`
  );
}

// --- Runtime failure diagnosis (#241, Phase 5) ---

export interface DiagnosisCard {
  kind: "job" | "held";
  /** The step that failed, named as the workflow names it. "" for holds. */
  step: string;
  /** What went wrong, in plain language. */
  cause: string;
  /** What to do about it. Never empty — a cause without an action isn't help. */
  action: string;
  exitcode: number | string | null;
  /** Raw stderr or hold reason. Kept, never led with. */
  technical: string;
}

export async function getRunDiagnosis(runId: string) {
  return request<{ run_id: string; cards: DiagnosisCard[]; total: number }>(
    `/workflows/${encodeURIComponent(runId)}/diagnosis`
  );
}

export interface RunEstimate {
  remaining_seconds: number;
  method: string;
  confidence: "low" | "medium" | "high";
  coverage: number;
  remaining_jobs: number;
  parallelism: number;
}

export async function getWorkflowEstimate(id: string) {
  return request<{ run_id: string; estimate: RunEstimate | null }>(
    `/workflows/${encodeURIComponent(id)}/estimate`
  );
}

// --- Dashboard ---

export interface DashboardData {
  ai: {
    skills: SkillMeta[];
    agents: AgentInfo[];
  };
  projects: { total: number; by_status: Record<string, number> };
  runs: {
    total: number;
    by_status: Record<string, number>;
    recent: {
      run_id: string;
      run_name: string;
      name: string;
      status: string;
      total_jobs: number;
      completed_jobs: number;
      failed_jobs: number;
      timestamp: string | null;
    }[];
  };
  resources: ProvisioningStatus;
  versions: { pegasus: string | null; condor: string | null };
  notifications: { unread: number };
}

export async function getDashboard() {
  return request<DashboardData>("/dashboard");
}

// --- Provisioning ---

export interface PoolBucket {
  label?: string;
  total_cpus: number;
  idle_cpus: number;
  total_gpus: number;
  idle_gpus: number;
  total_memory_mb: number;
  idle_memory_mb: number;
  machines: number;
  slots: number;
}

export interface PoolSummary extends PoolBucket {
  available: boolean;
  error?: string;
  pools: Record<string, PoolBucket>;
}

export interface WorkflowDemand {
  name: string | null;
  idle_jobs: number;
  running_jobs: number;
  held_jobs: number;
  idle_cpus_requested: number;
  idle_gpus_requested: number;
  idle_memory_mb_requested: number;
  oldest_idle_seconds: number | null;
  held_reason: string | null;
}

export interface QueueDemand {
  available: boolean;
  error?: string;
  total_jobs: number;
  idle_jobs: number;
  running_jobs: number;
  held_jobs: number;
  idle_cpus_requested: number;
  idle_gpus_requested: number;
  idle_memory_mb_requested: number;
  oldest_idle_seconds: number | null;
  held_reasons: string[];
  by_workflow: Record<string, WorkflowDemand>;
}

export interface PressureFinding {
  kind: string;
  severity: string;
  title: string;
  detail: string;
  delta: Record<string, number>;
}

export interface ProvisioningStatus {
  available: boolean;
  pool: Partial<PoolSummary> & { available: boolean; error?: string };
  demand: Partial<QueueDemand> & { available: boolean; error?: string };
  pressure: PressureFinding[];
  // OSPool (OSG) — user-scoped: capacity available to burst into + this user's
  // own claimed slots. Other users' tasks are excluded. Own longer cache.
  ospool?: {
    available: boolean;
    error?: string;
    cm?: string | null;
    owner?: string;
    avail_cpus?: number;
    avail_gpus?: number;
    avail_memory_mb?: number;
    avail_slots?: number;
    mine?: {
      cpus: number;
      gpus: number;
      memory_mb: number;
      slots: number;
      machines: number;
    };
  };
  checked_at?: number;
}

export async function getProvisioningStatus() {
  return request<ProvisioningStatus>("/provisioning/status");
}

// --- Marketplace (PegasusHub + Pegasus-ISI workflows from GitHub) ---

export interface MarketplaceWorkflow {
  id: string;
  name: string;
  description: string;
  topic: string;
  repo_url: string;
  image: string;
  license: string;
  pegasus_version: string;
  source: string; // "PegasusHub" | "Pegasus-ISI"
}

export async function getMarketplace() {
  return request<{ workflows: MarketplaceWorkflow[]; count: number }>(
    "/marketplace"
  );
}

export async function addMarketplaceWorkflow(id: string) {
  return request<{
    status: string;
    project_id: string;
    project_dir: string;
    repo_url: string;
  }>(`/marketplace/${encodeURIComponent(id)}/add`, { method: "POST" });
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

export interface MarketplaceStatus {
  path: string;
  exists: boolean;
  is_git: boolean;
  sha: string | null;
  short_sha: string | null;
  subject: string | null;
  committed_at: string | null;
  dirty: boolean;
  ahead: number;
  skills: number;
  updated?: boolean;
  reason?: string | null;
}

/** Which marketplace checkout this session reads, and at which commit. */
export async function getMarketplaceStatus() {
  return request<MarketplaceStatus>("/knowledge/marketplace");
}

/** Fast-forward the checkout. Declines (rather than discards) local changes. */
export async function updateMarketplace() {
  return request<MarketplaceStatus>("/knowledge/marketplace/update", {
    method: "POST",
  });
}

export async function getSkills() {
  return request<{ skills: SkillMeta[] }>("/knowledge/skills");
}

export async function getAgents() {
  return request<{ agents: AgentInfo[] }>("/knowledge/agents");
}

// --- Files ---

// --- Data upload (#241, Phase 2) ---

export interface UploadLimit {
  max_bytes: number;
  free_bytes: number;
  /** Absolute path of the default upload directory, for display. */
  data_dir: string;
}

export interface UploadedFile {
  path: string;
  name: string;
  size: number;
  /** Workspace-relative — what to show, and what to paste into a run field. */
  relative_path: string;
}

export async function getUploadLimit() {
  return request<UploadLimit>("/files/upload-limit");
}

/**
 * Upload one file, reporting progress.
 *
 * XMLHttpRequest rather than fetch: fetch cannot report *upload* progress, and
 * a data file large enough to matter is exactly the case where a progress bar
 * is the difference between waiting and assuming it hung.
 */
export function uploadFile(
  file: File,
  opts: {
    dest?: string;
    onProgress?: (fraction: number) => void;
    signal?: AbortSignal;
  } = {}
): Promise<UploadedFile> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    if (opts.dest) form.append("dest", opts.dest);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", apiUrl("files/upload"));

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) opts.onProgress?.(e.loaded / e.total);
    };

    xhr.onload = () => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(xhr.responseText);
      } catch {
        parsed = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(parsed as UploadedFile);
        return;
      }
      // Surface the backend's message (size limit, disk full, bad name) —
      // those are written for the user and are the whole point of the errors.
      const detail =
        parsed && typeof (parsed as { detail?: unknown }).detail === "string"
          ? (parsed as { detail: string }).detail
          : `Upload failed (${xhr.status})`;
      reject(new Error(detail));
    };

    xhr.onerror = () =>
      reject(new Error("Upload failed — check your connection and try again."));
    xhr.onabort = () => reject(new Error("Upload cancelled"));
    opts.signal?.addEventListener("abort", () => xhr.abort());

    xhr.send(form);
  });
}

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

export async function deleteFile(path: string) {
  return request<{ status: string }>(
    `/files?path=${encodeURIComponent(path)}`,
    { method: "DELETE" }
  );
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
  id?: number | null;
  role: string;
  content: string;
  agent_id?: string | null;
  tool_calls?: unknown;
  created_at?: string | null;
  feedback?: number | null; // 1 = useful, -1 = not useful
}

export async function setChatFeedback(messageId: number, value: 1 | -1 | 0) {
  return request<{ status: string; message_id: number; value: number }>(
    "/chat/feedback",
    { method: "POST", body: JSON.stringify({ message_id: messageId, value }) }
  );
}

export async function getChatHistory(limit = 100, conversationId?: string) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (conversationId) params.set("conversation_id", conversationId);
  return request<{ messages: ChatMessage[] }>(`/chat/history?${params}`);
}

export async function stopChat(requestId?: string) {
  return request<{ status: string }>("/chat/stop", {
    method: "POST",
    body: JSON.stringify({ request_id: requestId }),
  });
}

// --- Command approval (SEC-4 per-turn confirmation) ---

// Resolve a pending shell-command approval prompt mid-stream.
export async function sendChatPermission(
  requestId: string,
  callId: string,
  decision: "approve" | "deny"
) {
  return request<{ status: string; call_id: string }>("/chat/permission", {
    method: "POST",
    body: JSON.stringify({
      request_id: requestId,
      call_id: callId,
      decision,
    }),
  });
}

export async function getCommandApproval() {
  return request<{ require_approval: boolean }>("/chat/command-approval");
}

export async function updateCommandApproval(requireApproval: boolean) {
  return request<{ require_approval: boolean }>("/chat/command-approval", {
    method: "PUT",
    body: JSON.stringify({ require_approval: requireApproval }),
  });
}

// --- Jupyter ---

export async function getJupyterStatus() {
  return request<{ status: string; port: number | null }>("/jupyter/status");
}

// --- Notifications ---

export type NotificationSeverity = "info" | "success" | "warning" | "error";

export interface Notification {
  id: string;
  kind: string; // workflow_state | job_failure | resource | manual
  severity: NotificationSeverity;
  title: string;
  body: string | null;
  run_id: string | null;
  link: string | null;
  meta: Record<string, unknown>;
  read: boolean;
  created_at: string;
}

export interface NotificationList {
  notifications: Notification[];
  unread: number;
}

export async function getNotifications(limit = 100, unreadOnly = false) {
  return request<NotificationList>(
    `/notifications?limit=${limit}&unread_only=${unreadOnly}`
  );
}

export async function createNotification(data: {
  title: string;
  body?: string;
  kind?: string;
  severity?: NotificationSeverity;
  run_id?: string;
  link?: string;
  dedup_key?: string;
}) {
  return request<Notification>("/notifications", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function markNotificationRead(id: string) {
  return request<{ status: string }>(`/notifications/${id}/read`, {
    method: "POST",
  });
}

export async function markAllNotificationsRead() {
  return request<{ status: string }>("/notifications/read-all", {
    method: "POST",
  });
}

export async function deleteNotification(id: string) {
  return request<{ status: string }>(`/notifications/${id}`, {
    method: "DELETE",
  });
}

export async function clearNotifications() {
  return request<{ status: string }>("/notifications", { method: "DELETE" });
}

export interface NotificationPrefs {
  muted: string[]; // notification kinds that are suppressed
}

export async function getNotificationPrefs() {
  return request<NotificationPrefs>("/notifications/prefs");
}

export async function updateNotificationPrefs(muted: string[]) {
  return request<NotificationPrefs>("/notifications/prefs", {
    method: "PUT",
    body: JSON.stringify({ muted }),
  });
}
