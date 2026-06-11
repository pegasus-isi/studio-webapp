"""Pydantic schemas for studio-api requests and responses."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# --- Enums ---


class ToolStatus(str, enum.Enum):
    INSTALLED = "installed"
    RUNNING = "running"
    ERROR = "error"


class ToolType(str, enum.Enum):
    TERMINAL = "terminal"
    WEB = "web"


class WorkflowStatus(str, enum.Enum):
    PLANNING = "planning"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ProjectStatus(str, enum.Enum):
    DRAFT = "draft"
    GENERATED = "generated"
    PLANNED = "planned"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class LLMProvider(str, enum.Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"
    FABRIC = "fabric"
    NRP = "nrp"
    CUSTOM = "custom"
    OLLAMA = "ollama"


# --- Tool models ---


class ToolInfo(BaseModel):
    id: str
    name: str
    vendor: str
    description: str
    version_command: str | None = None
    install_method: str = "npm"
    install_command: str | None = None
    binary: str | None = None
    type: ToolType = ToolType.TERMINAL
    web_command: str | None = None
    supports_mcp: bool = False
    supports_web: bool = False
    required_env: list[str] = Field(default_factory=list)
    knowledge_adapter: str | None = None
    icon: str | None = None
    homepage: str | None = None


class ToolInstallation(BaseModel):
    tool_id: str
    status: ToolStatus
    config: dict[str, Any] = Field(default_factory=dict)
    process_pid: int | None = None
    web_port: int | None = None
    installed_at: datetime
    updated_at: datetime


class ToolDetailResponse(BaseModel):
    """Tool registry info merged with installation status."""
    info: ToolInfo
    installed: bool = False
    status: ToolStatus | None = None
    process_pid: int | None = None
    web_port: int | None = None


class InstallResponse(BaseModel):
    tool_id: str
    status: str


# --- LLM models ---


class LLMConfigRequest(BaseModel):
    provider: LLMProvider
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    extra_config: dict[str, Any] = Field(default_factory=dict)


class LLMConfigResponse(BaseModel):
    provider: str
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    extra_config: dict[str, Any] = Field(default_factory=dict)
    updated_at: str | None = None


class ProviderInfo(BaseModel):
    id: str
    name: str
    base_url: str | None = None
    default_model: str
    api_key_env: str | None = None


class ValidateRequest(BaseModel):
    provider: LLMProvider
    api_key: str
    base_url: str | None = None


class ValidateResponse(BaseModel):
    valid: bool
    models: list[str] = Field(default_factory=list)
    error: str | None = None


class ProviderConfigRequest(BaseModel):
    """Request to create/update a saved provider configuration."""
    provider_id: str
    name: str
    api_key: str = ""
    base_url: str = ""
    default_model: str = ""
    is_active: bool = False


class ProviderConfigResponse(BaseModel):
    provider_id: str
    name: str
    api_key: str = ""
    base_url: str = ""
    default_model: str = ""
    is_active: bool = False
    updated_at: str | None = None


# --- Workflow models ---


class WorkflowRunResponse(BaseModel):
    run_id: str
    name: str
    run_dir: str
    status: WorkflowStatus
    total_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    exec_site: str | None = None
    created_at: str
    updated_at: str


class WorkflowListResponse(BaseModel):
    workflows: list[WorkflowRunResponse]


class WorkflowProjectRunResponse(BaseModel):
    run_id: str
    name: str
    run_dir: str
    status: str


class WorkflowProjectResponse(BaseModel):
    project_id: str
    name: str
    project_dir: str
    status: ProjectStatus
    has_generator: bool = False
    has_workflow_yml: bool = False
    has_dockerfile: bool = False
    runs: list[WorkflowProjectRunResponse] = Field(default_factory=list)


class WorkflowProjectListResponse(BaseModel):
    projects: list[WorkflowProjectResponse]


# --- File models ---


class FileEntry(BaseModel):
    name: str
    type: str  # "file" or "dir"
    size: int | None = None
    modified: str | None = None


class FileListResponse(BaseModel):
    path: str
    entries: list[FileEntry]


class FileReadResponse(BaseModel):
    path: str
    content: str
    size: int


class FileWriteRequest(BaseModel):
    path: str
    content: str


class MkdirRequest(BaseModel):
    path: str


# --- Knowledge models ---


class SkillMetadata(BaseModel):
    name: str
    description: str = ""
    slash_command: str | None = None


class SkillResponse(BaseModel):
    name: str
    content: str
    metadata: SkillMetadata


class AgentInfo(BaseModel):
    id: str
    name: str
    description: str = ""


# --- Health models ---


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str


class DetailedHealthResponse(BaseModel):
    status: str = "ok"
    version: str
    db_ok: bool = False
    pegasus_version: str | None = None
    condor_version: str | None = None


# --- Settings models ---


class SettingsResponse(BaseModel):
    llm: LLMConfigResponse | None = None
    installed_tools: list[str] = Field(default_factory=list)


# --- Chat models ---


class ChatMessage(BaseModel):
    role: str
    content: str
    agent_id: str | None = None
    tool_calls: dict[str, Any] | None = None
    created_at: str | None = None


class ChatHistoryResponse(BaseModel):
    messages: list[ChatMessage]


# --- Jupyter models ---


class JupyterStatusResponse(BaseModel):
    status: str  # "running" | "stopped" | "starting"
    port: int | None = None
