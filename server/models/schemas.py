"""
schemas.py — Pydantic models for API request/response validation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field


# ── Requests ───────────────────────────────────────────────────────────────

class CreateChatRequest(BaseModel):
    title: str = "New Chat"


class RenameChatRequest(BaseModel):
    title: str


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1)
    file_ids: list[str] = Field(default_factory=list)


# ── Responses ──────────────────────────────────────────────────────────────

class ChatSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: datetime
    updated_at: Optional[datetime] = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str
    content: Any
    token_count: int = 0
    created_at: datetime


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    size_bytes: int
    in_context: bool = True
    created_at: datetime


class ChatDetail(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    sandbox_id: Optional[str] = None
    messages: list[MessageOut] = Field(default_factory=list)
    files: list[FileOut] = Field(default_factory=list)


class HealthResponse(BaseModel):
    app: str = "ok"
    trino: str = "disabled"
    database: str = "ok"


class OkResponse(BaseModel):
    ok: bool = True
