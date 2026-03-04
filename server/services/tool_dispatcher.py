"""
tool_dispatcher.py — Routes tool calls to the correct executor.

Extracted from the Streamlit app.py tool_exec closure. This is a pure function
with no framework dependencies — it takes all dependencies as arguments.
"""

from __future__ import annotations

from typing import Any, Optional

from src.code_executor import CodeExecutor
from src.trino_tool_handler import TrinoToolHandler


def dispatch_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    chat_id: str,
    sandbox_id: str | None,
    message_id: str,
    iteration: int,
    executor: CodeExecutor | None,
    trino: TrinoToolHandler | None,
    db: Any,
) -> dict[str, Any]:
    """
    Route a tool call to the correct executor and return a result dict.

    Returns:
        {"success": bool, "output": str, "error": str|None, "sandbox_id": str|None}
    """
    try:
        # ── Sandbox tools ──────────────────────────────────────────
        if tool_name == "create_file":
            if not executor:
                return _no_executor()
            if "path" not in tool_input:
                return _missing("path")
            if "content" not in tool_input:
                return _missing("content")
            return executor.create_file(
                chat_id, sandbox_id,
                tool_input["path"], tool_input["content"],
                message_id=message_id, iteration=iteration,
            )

        elif tool_name == "read_file":
            if not executor:
                return _no_executor()
            if "path" not in tool_input:
                return _missing("path")
            return executor.read_file(
                chat_id, sandbox_id, tool_input["path"],
                message_id=message_id, iteration=iteration,
            )

        elif tool_name == "list_files":
            if not executor:
                return _no_executor()
            directory = tool_input.get("directory", "/home/user")
            return executor.list_files(
                chat_id, sandbox_id, directory,
                message_id=message_id, iteration=iteration,
            )

        elif tool_name == "execute_python":
            if not executor:
                return _no_executor()
            code = tool_input.get("code")
            file_path = tool_input.get("file_path")
            if not code and not file_path:
                return {"success": False, "error": "Must provide either 'code' or 'file_path'"}
            return executor.execute_python(
                chat_id, sandbox_id,
                code=code, file_path=file_path,
                message_id=message_id, iteration=iteration,
            )

        elif tool_name == "execute_bash":
            if not executor:
                return _no_executor()
            command = tool_input.get("command")
            if not command:
                return _missing("command")
            return executor.execute_bash(
                chat_id, sandbox_id, command,
                message_id=message_id, iteration=iteration,
            )

        elif tool_name == "save_files":
            if not executor:
                return _no_executor()
            files_list = tool_input.get("files", [])
            if not files_list:
                return _missing("files")
            return executor.save_files(
                chat_id, sandbox_id, files_list,
                message_id=message_id, iteration=iteration,
            )

        # ── Trino lakehouse tools ──────────────────────────────────
        elif tool_name in ("health_check", "list_schemas", "list_tables",
                           "describe_table", "run_query"):
            if not trino:
                return {
                    "success": False,
                    "error": "Trino integration not configured. "
                             "Check config.yaml trino.enabled and MCP server status.",
                }
            return trino.execute_tool(
                tool_name=tool_name,
                tool_input=tool_input,
                chat_id=chat_id,
                sandbox_id=sandbox_id,
                message_id=message_id,
                iteration=iteration,
            )

        else:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "error": f"Tool execution error: {str(e)}"}


def _no_executor() -> dict:
    return {"success": False, "error": "Code executor not available"}


def _missing(param: str) -> dict:
    return {"success": False, "error": f"Missing required parameter: '{param}'"}
