"""
deps.py — Dependency injection for FastAPI.

All heavy objects (DB, LLM client, code executor, MCP client) are created once
at startup and accessed via FastAPI's Depends() mechanism.
"""

from __future__ import annotations

import os
from typing import Optional

from src.database import Database
from src.llm_client import LLMClient
from src.code_executor import CodeExecutor
from src.file_handler import FileHandler
from src.mcp_client import MCPTrinoClient
from src.trino_tool_handler import TrinoToolHandler

from .config import get_config


# ── Singleton instances ────────────────────────────────────────────────────

db: Optional[Database] = None
llm_client: Optional[LLMClient] = None
code_executor: Optional[CodeExecutor] = None
file_handler: Optional[FileHandler] = None
mcp_client: Optional[MCPTrinoClient] = None
trino_handler: Optional[TrinoToolHandler] = None


def init_all():
    """Initialize all dependencies. Called once at FastAPI startup."""
    global db, llm_client, code_executor, file_handler, mcp_client, trino_handler

    config = get_config()

    # Database
    db = Database(config["database"]["path"])
    print(f"[DEPS] Database initialized: {config['database']['path']}")

    # File handler
    file_handler = FileHandler(config["storage"]["uploads_dir"])
    print(f"[DEPS] FileHandler initialized: {config['storage']['uploads_dir']}")

    # Code executor
    if config["code_execution"]["enabled"]:
        try:
            code_executor = CodeExecutor(
                timeout_seconds=config["code_execution"]["timeout_seconds"],
                db=db,
            )
            print("[DEPS] CodeExecutor initialized")
        except Exception as e:
            print(f"[DEPS] CodeExecutor failed: {e}")
            code_executor = None
    else:
        code_executor = None

    # LLM client
    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_BASE_URL")
    if not api_key:
        raise RuntimeError("LLM_API_KEY not set in environment")

    llm_client = LLMClient(
        api_key=api_key,
        model=config["llm"]["model"],
        max_tokens=config["llm"]["max_tokens"],
        base_url=base_url,
        endpoint_name=config["llm"]["endpoint_name"],
        db=db,
    )
    print(f"[DEPS] LLMClient initialized: {config['llm']['endpoint_name']}")

    # MCP Trino client
    if config.get("trino", {}).get("enabled", False):
        try:
            mcp_client = MCPTrinoClient(
                mcp_url=config["trino"]["mcp_url"],
                default_username=config["trino"]["default_user"],
            )
            trino_handler = TrinoToolHandler(
                mcp_client=mcp_client,
                code_executor=code_executor,
                db=db,
            )
            print("[DEPS] Trino MCP integration enabled")
        except Exception as e:
            print(f"[DEPS] Trino MCP failed: {e}")
            mcp_client = None
            trino_handler = None
    else:
        mcp_client = None
        trino_handler = None


def shutdown_all():
    """Cleanup on shutdown."""
    global mcp_client
    if mcp_client:
        mcp_client.close()
        mcp_client = None
    print("[DEPS] Shutdown complete")


# ── FastAPI dependency getters ─────────────────────────────────────────────

def get_db() -> Database:
    return db

def get_llm() -> LLMClient:
    return llm_client

def get_executor() -> Optional[CodeExecutor]:
    return code_executor

def get_file_handler() -> FileHandler:
    return file_handler

def get_trino() -> Optional[TrinoToolHandler]:
    return trino_handler
