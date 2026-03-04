"""
mcp_client.py — Thin HTTP client for the Trino MCP server.

Connects to the MCP server over SSE transport and exposes the 5 Trino
tools as simple synchronous Python methods. The username for RBAC
impersonation is injected here — never exposed to the LLM.

Usage:
    client = MCPTrinoClient("http://localhost:8000", default_username="test@example.com")
    schemas = client.list_schemas()
    result  = client.run_query("SELECT * FROM catalog.schema.table LIMIT 10")
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from fastmcp import Client


class MCPTrinoClient:
    """Synchronous wrapper around the async FastMCP client for Trino MCP tools."""

    def __init__(self, mcp_url: str, default_username: str | None = None):
        """
        Args:
            mcp_url:          Base URL of the MCP server SSE endpoint
                              (e.g. "http://localhost:8000").
            default_username: User identity for RBAC impersonation.
                              None = use MCP server's service account (no impersonation).
        """
        # FastMCP Client expects the /sse path for SSE transport
        self.mcp_url = mcp_url.rstrip("/")
        self.sse_url = f"{self.mcp_url}/sse"
        self.default_username = default_username

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        print(f"[MCP-CLIENT] Initialized → {self.mcp_url} (user: {self.default_username or 'service account'})")

    # ------------------------------------------------------------------
    # Internal: run async MCP calls synchronously
    # ------------------------------------------------------------------

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create an event loop for running async calls."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """
        Call an MCP tool synchronously.

        Opens a fresh client connection per call. FastMCP Client handles
        the SSE handshake, JSON-RPC framing, and response parsing.
        """
        async def _run():
            async with Client(self.sse_url) as client:
                result = await client.call_tool(tool_name, arguments)
                return result

        loop = self._get_loop()
        try:
            result = loop.run_until_complete(_run())
            # Debug: log actual return type to help diagnose parsing issues
            print(f"[MCP-CLIENT] Raw result type: {type(result).__name__}, "
                  f"has .content: {hasattr(result, 'content')}, "
                  f"has .isError: {hasattr(result, 'isError')}")
            return result
        except Exception as e:
            raise ConnectionError(f"MCP call '{tool_name}' failed: {e}") from e

    def _extract_result(self, raw_result: Any) -> dict:
        """
        Extract a dict from the MCP tool response.

        FastMCP Client.call_tool() can return:
          - A CallToolResult object with .content (list) and .isError (bool)
          - A list of content objects (TextContent, etc.)
          - A dict (unlikely but defensive)
          - None

        Our MCP tools always return JSON-serialized dicts in a TextContent block.
        Guardrail errors also come as JSON with an "error" key.
        """
        import json

        if raw_result is None:
            return {"error": "Empty response from MCP server"}

        # ── Unwrap CallToolResult or similar wrapper objects ───────────
        # FastMCP 2.x returns a CallToolResult with .content and .isError
        is_error = getattr(raw_result, "isError", False) or getattr(raw_result, "is_error", False)
        content = getattr(raw_result, "content", None)

        if content is not None:
            # Unwrap: use the content list for parsing
            items = content
        elif isinstance(raw_result, list):
            items = raw_result
        elif isinstance(raw_result, dict):
            return raw_result
        elif isinstance(raw_result, str):
            # Rare: raw string response
            try:
                return json.loads(raw_result)
            except (json.JSONDecodeError, TypeError):
                return {"error": raw_result} if is_error else {"raw": raw_result}
        else:
            # Unknown type — try str conversion as last resort
            text = str(raw_result)
            print(f"[MCP-CLIENT] WARNING: Unexpected result type {type(raw_result).__name__}: {text[:200]}")
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return {"error": text} if is_error else {"raw": text}

        # ── Parse content blocks ──────────────────────────────────────
        if not items:
            return {"error": "Empty content in MCP response"}

        for item in items:
            text = getattr(item, "text", None)
            if not text:
                continue

            # Try JSON parse (happy path — all our tools return JSON dicts)
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    # If MCP flagged isError but dict lacks "error" key, inject it
                    if is_error and "error" not in parsed:
                        parsed["error"] = text
                    return parsed
                # Non-dict JSON (e.g. a list) — wrap it
                return {"data": parsed, "error": text} if is_error else {"data": parsed}
            except (json.JSONDecodeError, TypeError):
                pass

            # Non-JSON text — return as error or raw
            if is_error:
                return {"error": text, "validation_error": True}
            return {"raw": text}

        return {"error": "No text content in MCP response"}

    # ------------------------------------------------------------------
    # Public API — one method per MCP tool
    # ------------------------------------------------------------------

    def health_check(self) -> dict:
        """
        Check if the MCP server and Trino are reachable.

        Returns:
            {"server": "ok", "trino": "ok"|"unreachable", "catalog": str, ...}
        """
        t0 = time.monotonic()
        raw = self._call_tool("health_check", {})
        result = self._extract_result(raw)
        elapsed = (time.monotonic() - t0) * 1000
        print(f"[MCP-CLIENT] health_check → {result.get('trino', '?')} ({elapsed:.0f}ms)")
        return result

    def list_schemas(self, username: str | None = None) -> dict:
        """
        List all schemas in the configured catalog.

        Returns:
            {"catalog": str, "schemas": [...], "count": int}
        """
        user = username or self.default_username
        t0 = time.monotonic()
        args = {"username": user} if user else {}
        raw = self._call_tool("list_schemas", args)
        result = self._extract_result(raw)
        elapsed = (time.monotonic() - t0) * 1000
        print(f"[MCP-CLIENT] list_schemas → {result.get('count', '?')} schemas ({elapsed:.0f}ms)")
        return result

    def list_tables(self, schema: str, username: str | None = None) -> dict:
        """
        List all tables in a schema.

        Returns:
            {"catalog": str, "schema": str, "tables": [...], "count": int}
        """
        user = username or self.default_username
        t0 = time.monotonic()
        args = {"schema": schema}
        if user:
            args["username"] = user
        raw = self._call_tool("list_tables", args)
        result = self._extract_result(raw)
        elapsed = (time.monotonic() - t0) * 1000
        print(f"[MCP-CLIENT] list_tables({schema}) → {result.get('count', '?')} tables ({elapsed:.0f}ms)")
        return result

    def describe_table(self, schema: str, table: str, username: str | None = None) -> dict:
        """
        Get column definitions and approximate row count for a table.

        Returns:
            {"columns": [...], "column_count": int, "row_count": int|None, ...}
        """
        user = username or self.default_username
        t0 = time.monotonic()
        args = {"schema": schema, "table": table}
        if user:
            args["username"] = user
        raw = self._call_tool("describe_table", args)
        result = self._extract_result(raw)
        elapsed = (time.monotonic() - t0) * 1000
        print(f"[MCP-CLIENT] describe_table({schema}.{table}) → "
              f"{result.get('column_count', '?')} cols ({elapsed:.0f}ms)")
        return result

    def run_query(self, sql: str, limit: int = 100, username: str | None = None) -> dict:
        """
        Execute a validated SELECT query against Trino.

        Returns on success:
            {"columns": [...], "rows": [...], "row_count": int,
             "duration_ms": float, "truncated": bool, "limit_applied": int}

        Returns on guardrail block:
            {"error": str, "validation_error": True, "rows": [], "columns": []}

        Returns on execution error:
            {"error": str, "validation_error": False, "rows": [], "columns": []}
        """
        user = username or self.default_username
        t0 = time.monotonic()
        args = {"sql": sql, "limit": limit}
        if user:
            args["username"] = user
        raw = self._call_tool("run_query", args)
        result = self._extract_result(raw)
        elapsed = (time.monotonic() - t0) * 1000

        if result.get("error"):
            print(f"[MCP-CLIENT] run_query → ERROR: {result['error'][:100]} ({elapsed:.0f}ms)")
        else:
            print(f"[MCP-CLIENT] run_query → {result.get('row_count', '?')} rows ({elapsed:.0f}ms)")

        return result

    def close(self):
        """Cleanup event loop."""
        if self._loop and not self._loop.is_closed():
            self._loop.close()
            self._loop = None
