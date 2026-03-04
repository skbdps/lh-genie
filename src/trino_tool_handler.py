"""
trino_tool_handler.py — Bridge between the chatbot's tool dispatch and the MCP Trino server.

Responsibilities:
  1. Dispatches Trino tool calls from the LLM to the MCP client.
  2. Logs every call to the chatbot's DB (same as sandbox tools).
  3. For run_query: writes full results as JSON into the sandbox workspace
     so Python code can read them without stuffing rows into the LLM context.
  4. Returns summarized results to the LLM (preview + file path).

The username for RBAC impersonation is injected here, never controlled by the LLM.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from src.mcp_client import MCPTrinoClient


class TrinoToolHandler:
    """Executes Trino MCP tools and bridges query results into the sandbox."""

    # Tools handled by this class
    TRINO_TOOLS = frozenset({
        "health_check",
        "list_schemas",
        "list_tables",
        "describe_table",
        "run_query",
    })

    def __init__(self, mcp_client: MCPTrinoClient, code_executor, db):
        """
        Args:
            mcp_client:    Initialized MCPTrinoClient instance.
            code_executor: The chatbot's CodeExecutor (for workspace access).
            db:            The chatbot's Database instance (for tool call logging).
        """
        self.mcp = mcp_client
        self.executor = code_executor
        self.db = db

        # Per-chat query counter for sequential filenames
        self._query_counters: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public dispatch
    # ------------------------------------------------------------------

    def execute_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        chat_id: str,
        sandbox_id: str | None = None,
        message_id: str | None = None,
        iteration: int = 0,
    ) -> dict[str, Any]:
        """
        Execute a Trino tool and return a result dict compatible with
        the chatbot's tool_exec format:
            {"success": bool, "output": str, "error": str|None, "sandbox_id": str|None}
        """
        # Log start
        event_id = None
        if self.db:
            event_id = self.db.log_tool_call(
                chat_id=chat_id,
                tool_name=tool_name,
                tool_input=tool_input,
                message_id=message_id,
                iteration=iteration,
            )

        start = time.time()

        try:
            if tool_name == "health_check":
                result = self._handle_health_check()
            elif tool_name == "list_schemas":
                result = self._handle_list_schemas()
            elif tool_name == "list_tables":
                result = self._handle_list_tables(tool_input)
            elif tool_name == "describe_table":
                result = self._handle_describe_table(tool_input)
            elif tool_name == "run_query":
                result = self._handle_run_query(tool_input, chat_id, sandbox_id)
            else:
                result = {"success": False, "error": f"Unknown Trino tool: {tool_name}"}

        except ConnectionError as e:
            result = {
                "success": False,
                "error": f"MCP server connection failed: {e}",
                "sandbox_id": sandbox_id,
            }
        except Exception as e:
            result = {
                "success": False,
                "error": f"Trino tool error: {e}",
                "sandbox_id": sandbox_id,
            }

        elapsed_ms = (time.time() - start) * 1000

        # Log completion
        if self.db and event_id:
            self.db.update_tool_call(
                event_id=event_id,
                status="success" if result.get("success") else "error",
                tool_output={"output_length": len(result.get("output", ""))},
                error_msg=result.get("error"),
                sandbox_id=result.get("sandbox_id"),
                execution_time_ms=elapsed_ms,
            )

        return result

    # ------------------------------------------------------------------
    # Individual tool handlers
    # ------------------------------------------------------------------

    def _handle_health_check(self) -> dict:
        mcp_result = self.mcp.health_check()
        if mcp_result.get("error"):
            return {"success": False, "error": mcp_result["error"]}

        trino_status = mcp_result.get("trino", "unknown")
        catalog = mcp_result.get("catalog", "?")
        max_rows = mcp_result.get("max_rows", "?")
        timeout = mcp_result.get("query_timeout_seconds", "?")

        output = (
            f"Trino: {trino_status}\n"
            f"Catalog: {catalog}\n"
            f"Max rows per query: {max_rows}\n"
            f"Query timeout: {timeout}s"
        )
        return {"success": trino_status == "ok", "output": output}

    def _handle_list_schemas(self) -> dict:
        mcp_result = self.mcp.list_schemas()
        if mcp_result.get("error"):
            return {"success": False, "error": mcp_result["error"]}

        schemas = mcp_result.get("schemas", [])
        catalog = mcp_result.get("catalog", "?")
        output = f"Catalog: {catalog}\nSchemas ({len(schemas)}):\n"
        output += "\n".join(f"  - {s}" for s in schemas)
        return {"success": True, "output": output}

    def _handle_list_tables(self, tool_input: dict) -> dict:
        schema = tool_input.get("schema")
        if not schema:
            return {"success": False, "error": "Missing required parameter: 'schema'"}

        mcp_result = self.mcp.list_tables(schema)
        if mcp_result.get("error"):
            return {"success": False, "error": mcp_result["error"]}

        tables = mcp_result.get("tables", [])
        catalog = mcp_result.get("catalog", "?")
        output = f"Tables in {catalog}.{schema} ({len(tables)}):\n"
        output += "\n".join(f"  - {t}" for t in tables)
        return {"success": True, "output": output}

    def _handle_describe_table(self, tool_input: dict) -> dict:
        schema = tool_input.get("schema")
        table = tool_input.get("table")
        if not schema or not table:
            return {"success": False, "error": "Missing required parameters: 'schema' and 'table'"}

        mcp_result = self.mcp.describe_table(schema, table)
        if mcp_result.get("error"):
            return {"success": False, "error": mcp_result["error"]}

        columns = mcp_result.get("columns", [])
        row_count = mcp_result.get("row_count")
        catalog = mcp_result.get("catalog", "?")

        lines = [f"Table: {catalog}.{schema}.{table}"]
        if row_count is not None:
            lines.append(f"Approximate rows: {row_count:,}")
        lines.append(f"Columns ({len(columns)}):")

        # Format as aligned table
        if columns:
            max_name = max(len(c.get("name", "")) for c in columns)
            max_type = max(len(c.get("type", "")) for c in columns)
            for col in columns:
                name = col.get("name", "?").ljust(max_name)
                dtype = col.get("type", "?").ljust(max_type)
                comment = col.get("comment") or ""
                comment_str = f"  -- {comment}" if comment else ""
                lines.append(f"  {name}  {dtype}{comment_str}")

        return {"success": True, "output": "\n".join(lines)}

    def _handle_run_query(
        self,
        tool_input: dict,
        chat_id: str,
        sandbox_id: str | None,
    ) -> dict:
        sql = tool_input.get("sql")
        if not sql:
            return {"success": False, "error": "Missing required parameter: 'sql'"}

        limit = tool_input.get("limit", 100)

        mcp_result = self.mcp.run_query(sql, limit=limit)

        # Handle errors
        if mcp_result.get("error"):
            is_validation = mcp_result.get("validation_error", False)
            prefix = "Query blocked by guardrails" if is_validation else "Query execution error"
            return {
                "success": False,
                "error": f"{prefix}: {mcp_result['error']}",
                "sandbox_id": sandbox_id,
            }

        columns = mcp_result.get("columns", [])
        rows = mcp_result.get("rows", [])
        row_count = mcp_result.get("row_count", 0)
        duration_ms = mcp_result.get("duration_ms", 0)
        truncated = mcp_result.get("truncated", False)

        # Write full results to sandbox as JSON
        file_path = None
        if rows and self.executor:
            file_path = self._write_results_to_sandbox(
                chat_id=chat_id,
                sandbox_id=sandbox_id,
                columns=columns,
                rows=rows,
                sql=sql,
                duration_ms=duration_ms,
            )

        # Build summary for LLM context (NOT the full dataset)
        summary_parts = [
            f"Query returned {row_count:,} row(s) in {duration_ms:.0f}ms.",
        ]

        if truncated:
            summary_parts.append(f"⚠️ Results truncated at {mcp_result.get('limit_applied', '?')} rows.")

        # Column listing
        summary_parts.append(f"Columns: {', '.join(columns)}")

        # First 5 rows as text preview
        preview_rows = rows[:5]
        if preview_rows:
            summary_parts.append("\nPreview (first 5 rows):")
            # Simple aligned text table
            for i, row in enumerate(preview_rows):
                vals = [f"{k}={_fmt_preview_val(v)}" for k, v in row.items()]
                summary_parts.append(f"  [{i+1}] {', '.join(vals)}")

        if row_count > 5:
            summary_parts.append(f"  ... ({row_count - 5} more rows)")

        # File path for code access
        if file_path:
            summary_parts.append(
                f"\nFull results saved to: {file_path}\n"
                f"Load with:\n"
                f"  import json\n"
                f"  from lib.lakehouse_utils import query_to_df\n"
                f"  with open('{file_path}') as f:\n"
                f"      data = json.load(f)\n"
                f"  df = query_to_df(data['rows'], data['columns'])"
            )

        return {
            "success": True,
            "output": "\n".join(summary_parts),
            "sandbox_id": sandbox_id,
        }

    # ------------------------------------------------------------------
    # Query results → sandbox file
    # ------------------------------------------------------------------

    def _write_results_to_sandbox(
        self,
        chat_id: str,
        sandbox_id: str | None,
        columns: list[str],
        rows: list[dict],
        sql: str,
        duration_ms: float,
    ) -> str | None:
        """
        Write query results as a JSON file into the sandbox workspace.

        Uses the same host-path write strategy as code_executor.create_file():
        the workspace directory is bind-mounted into the container, so writing
        to the host path makes the file instantly visible inside the container.

        Returns:
            The in-container path (e.g. "/home/user/query_results/query_001.json")
            or None on failure.
        """
        try:
            workspace = self.executor.get_workspace(chat_id)
            query_dir = workspace / "query_results"
            query_dir.mkdir(parents=True, exist_ok=True)

            # Sequential counter per chat
            counter = self._query_counters.get(chat_id, 0) + 1

            # Also check existing files in case counter is stale
            existing = list(query_dir.glob("query_*.json"))
            if existing:
                nums = []
                for f in existing:
                    try:
                        nums.append(int(f.stem.split("_")[1]))
                    except (IndexError, ValueError):
                        pass
                if nums:
                    counter = max(counter, max(nums) + 1)

            self._query_counters[chat_id] = counter
            filename = f"query_{counter:03d}.json"
            file_path = query_dir / filename

            payload = {
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "sql": sql,
                "duration_ms": duration_ms,
            }
            file_path.write_text(
                json.dumps(payload, default=str, ensure_ascii=False),
                encoding="utf-8",
            )

            container_path = f"/home/user/query_results/{filename}"
            size = file_path.stat().st_size
            print(f"[TRINO-HANDLER] Results → {container_path} ({size:,} bytes, {len(rows)} rows)")

            # Ensure sandbox container exists (so the file is accessible)
            if self.executor:
                container = self.executor.get_or_create_sandbox(chat_id, sandbox_id)

            return container_path

        except Exception as e:
            print(f"[TRINO-HANDLER] Failed to write results to sandbox: {e}")
            return None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _fmt_preview_val(val: Any) -> str:
    """Format a value for the text preview sent to the LLM."""
    if val is None:
        return "NULL"
    if isinstance(val, str):
        if len(val) > 50:
            return f'"{val[:47]}..."'
        return f'"{val}"'
    if isinstance(val, float):
        return f"{val:,.2f}"
    return str(val)
