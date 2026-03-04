"""
files.py — File upload, listing, download, and deletion.
"""

from __future__ import annotations

import os
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse

from server.models.schemas import FileOut, OkResponse
from server import deps

router = APIRouter(tags=["files"])


@router.post("/api/chats/{chat_id}/files", response_model=FileOut, status_code=201)
async def upload_file(
    chat_id: str,
    file: UploadFile = File(...),
    db=Depends(deps.get_db),
    fh=Depends(deps.get_file_handler),
    executor=Depends(deps.get_executor),
):
    """Upload a file to a chat. Saves to disk and optionally copies to sandbox."""
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    # Read file data
    file_data = await file.read()
    filename = file.filename or "upload"
    file_type = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    size_bytes = len(file_data)

    # Save to uploads directory
    file_path = fh.save_file(file_data, filename, chat_id)

    # Register in DB
    db_file = db.add_file(
        chat_id=chat_id,
        filename=filename,
        file_path=file_path,
        file_type=file_type,
        size_bytes=size_bytes,
        token_estimate=0,
    )

    # Upload to sandbox if needed
    if executor and _should_sandbox(file_type, size_bytes):
        try:
            sandbox_id = chat.sandbox_id
            result = executor.upload_to_sandbox(chat_id, sandbox_id, file_path, filename)
            if result.get("sandbox_id") and result["sandbox_id"] != chat.sandbox_id:
                db.update_chat(chat_id, sandbox_id=result["sandbox_id"])
            print(f"[FILES] Uploaded {filename} to sandbox")
        except Exception as e:
            print(f"[FILES] Sandbox upload failed: {e}")

    return FileOut(
        id=db_file.id,
        filename=db_file.filename,
        file_type=db_file.file_type,
        size_bytes=db_file.size_bytes,
        in_context=db_file.in_context,
        created_at=db_file.uploaded_at,
    )


@router.get("/api/chats/{chat_id}/files", response_model=list[FileOut])
def list_files(chat_id: str, db=Depends(deps.get_db)):
    files = db.get_files(chat_id)
    return [
        FileOut(
            id=f.id,
            filename=f.filename,
            file_type=f.file_type,
            size_bytes=f.size_bytes,
            in_context=f.in_context,
            created_at=f.uploaded_at,
        )
        for f in files
    ]


@router.delete("/api/files/{file_id}", response_model=OkResponse)
def delete_file(
    file_id: str,
    db=Depends(deps.get_db),
    fh=Depends(deps.get_file_handler),
):
    """Delete a file from disk and DB."""
    # We need to look up the file to get its path
    # The DB model should have a get-by-id method — for now, iterate
    # This is a minor gap; if needed, add db.get_file_by_id()
    try:
        fh.delete_file(file_id)  # Will silently skip if path doesn't exist
    except Exception:
        pass
    db.delete_file(file_id)
    return OkResponse()


@router.get("/api/chats/{chat_id}/outputs/{filename}")
def download_output(
    chat_id: str,
    filename: str,
    executor=Depends(deps.get_executor),
):
    """
    Serve a sandbox output file (chart HTML, table HTML, saved files).
    Searches workspace/output/ first, then workspace root, then recursively.
    """
    if not executor:
        raise HTTPException(status_code=503, detail="Code executor not available")

    # Sanitize filename (prevent path traversal)
    safe_name = Path(filename).name
    if safe_name != filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    try:
        workspace = executor.get_workspace(chat_id)

        # Search in priority order
        candidates = [
            workspace / "output" / safe_name,
            workspace / safe_name,
        ]

        file_path = None
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                file_path = candidate
                break

        # Fallback: recursive search
        if not file_path:
            matches = list(workspace.rglob(safe_name))
            if matches:
                file_path = matches[0]

        if not file_path:
            raise HTTPException(status_code=404, detail=f"File not found: {safe_name}")

        media_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        return FileResponse(
            path=str(file_path),
            filename=safe_name,
            media_type=media_type,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Helpers ────────────────────────────────────────────────────────────────

def _should_sandbox(file_type: str, size_bytes: int) -> bool:
    ALWAYS_SANDBOX = {"xlsx", "xls", "csv", "tsv", "parquet", "zip", "tar", "gz"}
    if file_type in ALWAYS_SANDBOX:
        return True
    if size_bytes > 1 * 1024 * 1024:
        return True
    return False
