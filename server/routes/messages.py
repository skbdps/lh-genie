"""
messages.py — Send a message and stream the response via SSE.

This is the core endpoint. It:
  1. Saves the user message to DB
  2. Uploads files to sandbox if needed
  3. Kicks off the orchestrator (LLM → tool → LLM loop)
  4. Streams SSE events back to the frontend
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from src.utils import estimate_message_tokens

from server import deps
from server.config import get_config
from server.services.orchestrator import run_agent_loop

router = APIRouter(prefix="/api/chats", tags=["messages"])


@router.post("/{chat_id}/messages")
async def send_message(
    chat_id: str,
    body: dict,
    db=Depends(deps.get_db),
    llm=Depends(deps.get_llm),
    executor=Depends(deps.get_executor),
    trino=Depends(deps.get_trino),
    fh=Depends(deps.get_file_handler),
):
    """
    Send a user message and stream the assistant response as SSE.

    Body:
        {"content": "user text", "file_ids": ["id1", ...]}

    SSE events:
        status, tool_start, tool_result, html_output, text, thinking, error, done
    """
    config = get_config()
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    user_text = body.get("content", "").strip()
    file_ids = body.get("file_ids", [])

    if not user_text:
        raise HTTPException(status_code=400, detail="Empty message")

    # ── Build message content with file handling ───────────────────────

    message_content = []

    if file_ids:
        files = db.get_files(chat_id)
        file_map = {f.id: f for f in files}

        for fid in file_ids:
            file = file_map.get(fid)
            if not file:
                continue

            if _should_use_sandbox(file.file_type, file.size_bytes):
                sandbox_path = f"/home/user/uploads/{file.filename}"
                message_content.append({
                    "type": "sandbox_file_ref",
                    "filename": file.filename,
                    "sandbox_path": sandbox_path,
                    "file_type": file.file_type,
                    "size_bytes": file.size_bytes,
                })
            else:
                claude_format = fh.convert_to_claude_format(
                    file.file_path, file.file_type
                )
                if claude_format:
                    message_content.append(claude_format)

    message_content.append({"type": "text", "text": user_text})

    # ── Save user message ──────────────────────────────────────────────

    user_msg_tokens = estimate_message_tokens(
        message_content,
        config["context"]["token_estimation_ratio"],
    )
    user_message = db.add_message(
        chat_id=chat_id,
        role="user",
        content=message_content,
        token_count=user_msg_tokens,
    )
    message_id = user_message.id
    print(f"[MESSAGES] User message saved: {message_id}")

    # ── Stream response via SSE ────────────────────────────────────────

    async def event_generator():
        try:
            async for event in run_agent_loop(
                chat_id=chat_id,
                user_content=message_content,
                message_id=message_id,
                db=db,
                llm=llm,
                executor=executor,
                trino=trino,
                file_handler=fh,
                config=config,
            ):
                yield {
                    "event": event.event,
                    "data": json.dumps(event.data, default=str),
                }
        except Exception as e:
            print(f"[MESSAGES] SSE stream error: {e}")
            yield {
                "event": "error",
                "data": json.dumps({"message": str(e)}),
            }

    return EventSourceResponse(event_generator())


# ── Helpers ────────────────────────────────────────────────────────────────

def _should_use_sandbox(file_type: str, file_size: int) -> bool:
    """Decide whether a file should go to sandbox (code-first) or be embedded in context."""
    ALWAYS_SANDBOX = {"xlsx", "xls", "csv", "tsv", "parquet", "zip", "tar", "gz"}
    ALWAYS_EMBED = {"pdf", "png", "jpg", "jpeg", "webp"}

    if file_type in ALWAYS_SANDBOX:
        return True
    if file_type in ALWAYS_EMBED and file_size < 10 * 1024 * 1024:
        return False
    if file_size > 1 * 1024 * 1024:
        return True
    return False
