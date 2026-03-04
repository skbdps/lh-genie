"""
chats.py — Chat CRUD endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException

from server.models.schemas import (
    ChatSummary, ChatDetail, MessageOut, FileOut,
    CreateChatRequest, RenameChatRequest, OkResponse,
)
from server import deps

router = APIRouter(prefix="/api/chats", tags=["chats"])


@router.get("", response_model=list[ChatSummary])
def list_chats(db=Depends(deps.get_db)):
    chats = db.get_all_chats()
    return [
        ChatSummary(
            id=c.id,
            title=c.title,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in chats
    ]


@router.post("", response_model=ChatSummary, status_code=201)
def create_chat(body: CreateChatRequest = None, db=Depends(deps.get_db)):
    title = body.title if body else "New Chat"
    chat = db.create_chat(title=title)
    return ChatSummary(
        id=chat.id,
        title=chat.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
    )


@router.get("/{chat_id}", response_model=ChatDetail)
def get_chat(chat_id: str, db=Depends(deps.get_db)):
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    messages = db.get_messages(chat_id)
    files = db.get_files(chat_id)

    return ChatDetail(
        id=chat.id,
        title=chat.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        sandbox_id=chat.sandbox_id,
        messages=[
            MessageOut(
                id=m.id,
                role=m.role,
                content=m.content,
                token_count=m.token_count,
                created_at=m.created_at,
            )
            for m in messages
        ],
        files=[
            FileOut(
                id=f.id,
                filename=f.filename,
                file_type=f.file_type,
                size_bytes=f.size_bytes,
                in_context=f.in_context,
                created_at=f.created_at,
            )
            for f in files
        ],
    )


@router.patch("/{chat_id}", response_model=ChatSummary)
def rename_chat(chat_id: str, body: RenameChatRequest, db=Depends(deps.get_db)):
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    db.update_chat(chat_id, title=body.title)
    chat = db.get_chat(chat_id)
    return ChatSummary(
        id=chat.id,
        title=chat.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
    )


@router.delete("/{chat_id}", response_model=OkResponse)
def delete_chat(
    chat_id: str,
    db=Depends(deps.get_db),
    executor=Depends(deps.get_executor),
    fh=Depends(deps.get_file_handler),
):
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    # Cleanup sandbox
    if executor:
        try:
            executor.close_sandbox(chat_id)
        except Exception as e:
            print(f"[CHATS] Sandbox cleanup failed for {chat_id}: {e}")

    # Cleanup files
    fh.delete_chat_files(chat_id)

    # Delete from DB
    db.delete_chat(chat_id)

    return OkResponse()
