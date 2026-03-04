"""
orchestrator.py — The agentic loop, extracted from app.py.

Runs the LLM → tool dispatch → LLM loop and yields SSE events as they
happen. The loop itself is synchronous (LLMClient.send_message uses
requests), so we run it in a thread and bridge events via asyncio.Queue.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from src.database import Database
from src.llm_client import LLMClient
from src.code_executor import CodeExecutor
from src.trino_tool_handler import TrinoToolHandler
from src.file_handler import FileHandler
from src.utils import get_context_messages, estimate_message_tokens

from .tool_dispatcher import dispatch_tool


# ── SSE Event wrapper ──────────────────────────────────────────────────────

@dataclass
class SSEEvent:
    event: str           # status, tool_start, tool_result, html_output, text, thinking, error, done
    data: dict = field(default_factory=dict)


# ── Main entry point ───────────────────────────────────────────────────────

async def run_agent_loop(
    chat_id: str,
    user_content: list[dict[str, Any]],
    message_id: str,
    db: Database,
    llm: LLMClient,
    executor: CodeExecutor | None,
    trino: TrinoToolHandler | None,
    file_handler: FileHandler,
    config: dict,
) -> AsyncGenerator[SSEEvent, None]:
    """
    Run the full agentic loop for one user message and yield SSE events.

    Steps:
      1. Build conversation context from DB
      2. Start LLM call with a tool_executor callback
      3. The callback dispatches tools and pushes events to a queue
      4. Yield events from the queue as they arrive
      5. When done, save assistant message to DB and yield 'done'
    """
    queue: asyncio.Queue[SSEEvent | None] = asyncio.Queue()
    loop = asyncio.get_event_loop()

    # ── Build conversation context ─────────────────────────────────────

    all_messages = db.get_messages(chat_id)
    claude_messages = []
    for msg in all_messages:
        if msg.role == "user" and isinstance(msg.content, list):
            if any(block.get("type") == "tool_result" for block in msg.content):
                continue
        content = msg.content
        if isinstance(content, list):
            content = [b for b in content if b.get("type") != "tool_result_display"]
        claude_messages.append({"role": msg.role, "content": content})

    # Apply context window limit
    claude_messages = get_context_messages(
        claude_messages,
        config["context"]["max_tokens"],
        config["context"]["token_estimation_ratio"],
    )

    # Append current user message
    claude_messages.append({"role": "user", "content": user_content})

    # ── Get sandbox state ──────────────────────────────────────────────

    chat = db.get_chat(chat_id)
    sandbox_id_holder = [chat.sandbox_id if chat else None]

    # ── Tool executor callback (runs in thread) ────────────────────────

    def tool_exec(tool_name: str, tool_input: dict) -> dict:
        """
        Dispatch a tool, push SSE events to the queue, return result.
        Called by LLMClient.send_message() inside the worker thread.
        """
        iteration = tool_exec._counter
        tool_exec._counter += 1

        # Emit tool_start
        _put(queue, loop, SSEEvent("tool_start", {
            "tool": tool_name,
            "input": _safe_input(tool_name, tool_input),
            "iteration": iteration,
        }))

        # Dispatch
        result = dispatch_tool(
            tool_name=tool_name,
            tool_input=tool_input,
            chat_id=chat_id,
            sandbox_id=sandbox_id_holder[0],
            message_id=message_id,
            iteration=iteration,
            executor=executor,
            trino=trino,
            db=db,
        )

        # Update sandbox_id if changed
        if result.get("sandbox_id") and result["sandbox_id"] != sandbox_id_holder[0]:
            sandbox_id_holder[0] = result["sandbox_id"]
            db.update_chat(chat_id, sandbox_id=result["sandbox_id"])

        # Emit tool_result
        _put(queue, loop, SSEEvent("tool_result", {
            "tool": tool_name,
            "success": result.get("success", False),
            "output": _truncate(result.get("output", "")),
            "error": result.get("error"),
            "iteration": iteration,
        }))

        # Check for HTML outputs after execute_python
        if tool_name == "execute_python" and result.get("success") and executor:
            _emit_html_outputs(queue, loop, chat_id, executor, result.get("output", ""))

        return result

    tool_exec._counter = 0

    # ── Worker thread: runs the synchronous LLM loop ───────────────────

    result_holder: list[dict | Exception] = []

    def worker():
        try:
            _put(queue, loop, SSEEvent("status", {"type": "thinking"}))

            response = llm.send_message(
                messages=claude_messages,
                chat_id=chat_id,
                message_id=message_id,
                tool_executor=tool_exec if executor else None,
                max_iterations=config["code_execution"]["max_iterations"],
            )
            result_holder.append(response)
        except Exception as e:
            result_holder.append(e)
        finally:
            # Signal done
            _put(queue, loop, None)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    # ── Yield events as they arrive ────────────────────────────────────

    while True:
        event = await queue.get()
        if event is None:
            break
        yield event

    # ── Handle result ──────────────────────────────────────────────────

    thread.join(timeout=5)

    if not result_holder:
        yield SSEEvent("error", {"message": "LLM loop produced no result"})
        return

    result = result_holder[0]

    if isinstance(result, Exception):
        yield SSEEvent("error", {"message": str(result)})
        # Save error as assistant message
        db.add_message(
            chat_id=chat_id,
            role="assistant",
            content=[{"type": "text", "text": f"Error: {result}"}],
            token_count=0,
        )
        return

    # ── Emit final response ────────────────────────────────────────────

    # Thinking blocks
    for block in result.get("content", []):
        if block.get("type") == "thinking":
            yield SSEEvent("thinking", {"text": block.get("thinking", "")})

    # Text blocks
    final_text = ""
    for block in result.get("content", []):
        if block.get("type") == "text":
            text = block.get("text", "")
            if text.strip():
                final_text += text + "\n"
                yield SSEEvent("text", {"text": text})

    if result.get("max_iterations_reached"):
        yield SSEEvent("text", {
            "text": f"\n\n⚠️ Maximum iterations ({config['code_execution']['max_iterations']}) reached."
        })

    # ── Save assistant message to DB ───────────────────────────────────

    enriched_content = list(result.get("content", []))
    if result.get("tool_calls"):
        for tc in result["tool_calls"]:
            enriched_content.append({
                "type": "tool_result_display",
                "tool_name": tc["tool"],
                "tool_input": tc["input"],
                "result": tc["result"],
                "iteration": tc["iteration"],
            })

    assistant_tokens = result.get("usage", {}).get("output_tokens", 0)
    db.add_message(
        chat_id=chat_id,
        role="assistant",
        content=enriched_content,
        token_count=assistant_tokens,
    )

    # Auto-title on first message
    if final_text.strip():
        msgs = db.get_messages(chat_id)
        if len(msgs) <= 2:
            title = final_text.strip()[:80].split("\n")[0]
            db.update_chat(chat_id, title=title)

    # ── Done event ─────────────────────────────────────────────────────

    yield SSEEvent("done", {
        "message_id": message_id,
        "usage": result.get("usage", {}),
        "tool_call_count": len(result.get("tool_calls", [])),
    })


# ── Helpers ────────────────────────────────────────────────────────────────

def _put(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, event: SSEEvent | None):
    """Thread-safe put into an asyncio queue from a sync context."""
    asyncio.run_coroutine_threadsafe(queue.put(event), loop)


def _truncate(text: str, max_len: int = 3000) -> str:
    """Truncate output for SSE transmission."""
    if len(text) > max_len:
        return text[:max_len] + f"\n... ({len(text) - max_len} chars truncated)"
    return text


def _safe_input(tool_name: str, tool_input: dict) -> dict:
    """Trim large inputs (e.g. code blocks) for SSE display."""
    safe = dict(tool_input)
    for key in ("code", "content"):
        if key in safe and isinstance(safe[key], str) and len(safe[key]) > 500:
            safe[key] = safe[key][:500] + "..."
    return safe


def _emit_html_outputs(
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
    chat_id: str,
    executor: CodeExecutor,
    stdout: str,
):
    """
    After execute_python, check for HTML outputs and emit them as SSE events.
    Parses [OUTPUT_HTML:path] markers from stdout, falls back to scanning output/.
    """
    try:
        workspace = executor.get_workspace(chat_id)
    except Exception:
        return

    html_paths = []

    # Parse markers from stdout
    markers = re.findall(r"\[OUTPUT_HTML:(/home/user/[^\]]+\.html)\]", stdout or "")
    for container_path in markers:
        relative = container_path.replace("/home/user/", "", 1)
        host_path = workspace / relative
        if host_path.exists():
            html_paths.append(host_path)

    # Fallback: scan output/ directory
    if not html_paths:
        output_dir = workspace / "output"
        if output_dir.exists():
            html_paths = sorted(output_dir.glob("*.html"), key=lambda f: f.stat().st_mtime)

    for html_file in html_paths:
        try:
            html_content = html_file.read_text(encoding="utf-8")
            if not html_content.strip():
                continue

            kind = "chart" if "plotly" in html_content.lower() else \
                   "table" if "lh-table" in html_content.lower() else "html"

            _put(queue, loop, SSEEvent("html_output", {
                "path": html_file.name,
                "html": html_content,
                "kind": kind,
            }))
        except Exception as e:
            print(f"[ORCHESTRATOR] Failed to read {html_file}: {e}")
