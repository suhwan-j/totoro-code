"""Pydantic configuration models for TOTORO-CODE."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PermissionConfig(BaseModel):
    mode: Literal["default", "auto_approve", "read_only", "plan_only"] = (
        "default"
    )
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class MemoryConfig(BaseModel):
    auto_extract: bool = True
    extraction_threshold_tokens: int = 5000
    max_memory_entries: int = 500


class LoopConfig(BaseModel):
    max_turns: int = 200
    tool_timeout_seconds: int = 120
    api_timeout_seconds: int = 60
    stall_detection: bool = True


class SubagentConfig(BaseModel):
    max_concurrent: int = 5
    default_max_turns: int = 100
    default_timeout_seconds: int = 600
    hitl_propagation: bool = True


class ContextConfig(BaseModel):
    auto_compact_threshold: float = 0.7
    reactive_compact_threshold: float = 0.85
    emergency_compact_threshold: float = 0.95
    model_context_window: int | None = (
        None  # None = auto-detect from model name
    )


class SandboxConfig(BaseModel):
    mode: Literal["none", "restricted", "container"] = "none"
    allowed_hosts: list[str] = Field(default_factory=list)
    container_image: str = "totoro-code-sandbox:latest"


class AgentConfig(BaseModel):
    model: str = "claude-sonnet-4-5-20250929"
    fallback_model: str = "claude-haiku-4-5-20251001"
    provider: Literal["auto", "openrouter", "anthropic", "openai", "vllm"] = (
        "auto"
    )
    project_root: str = "."
    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)
    subagent: SubagentConfig = Field(default_factory=SubagentConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
