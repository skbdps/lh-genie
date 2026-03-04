"""
Database models and operations for the chatbot POC.
"""

import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, JSON, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session

Base = declarative_base()


class Chat(Base):
    """Chat session model"""
    __tablename__ = 'chats'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    message_count = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    sandbox_id = Column(String, nullable=True)  # E2B sandbox ID for persistent execution
    
    # Relationships
    messages = relationship("Message", back_populates="chat", cascade="all, delete-orphan")
    files = relationship("FileAttachment", back_populates="chat", cascade="all, delete-orphan")
    thinking_events = relationship("ThinkingEvent", back_populates="chat", cascade="all, delete-orphan")
    tool_calls = relationship("ToolCall", back_populates="chat", cascade="all, delete-orphan")
    sandbox_files = relationship("SandboxFile", back_populates="chat", cascade="all, delete-orphan")


class Message(Base):
    """Message model"""
    __tablename__ = 'messages'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    chat_id = Column(String, ForeignKey('chats.id'), nullable=False)
    role = Column(String, nullable=False)  # 'user' or 'assistant'
    content = Column(JSON, nullable=False)  # Full Claude API format
    timestamp = Column(DateTime, default=datetime.utcnow)
    token_count = Column(Integer, default=0)
    
    # Relationships
    chat = relationship("Chat", back_populates="messages")


class FileAttachment(Base):
    """File attachment model"""
    __tablename__ = 'files'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    chat_id = Column(String, ForeignKey('chats.id'), nullable=False)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_type = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    in_context = Column(Boolean, default=True)
    token_estimate = Column(Integer, default=0)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    chat = relationship("Chat", back_populates="files")


class ThinkingEvent(Base):
    """Track Claude's thinking process"""
    __tablename__ = 'thinking_events'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, ForeignKey('chats.id'), nullable=False)
    message_id = Column(String, nullable=True)  # Which message triggered this
    timestamp = Column(DateTime, default=datetime.utcnow)
    thinking_text = Column(String, nullable=False)  # Using String instead of Text
    signature = Column(String, nullable=True)
    iteration = Column(Integer, default=0)
    
    # Relationships
    chat = relationship("Chat", back_populates="thinking_events")


class ToolCall(Base):
    """Track tool execution (E2B and other tools)"""
    __tablename__ = 'tool_calls'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, ForeignKey('chats.id'), nullable=False)
    message_id = Column(String, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    iteration = Column(Integer, default=0)
    
    tool_name = Column(String, nullable=False)
    tool_input = Column(JSON, nullable=False)
    
    status = Column(String, default='pending')  # 'pending', 'success', 'error'
    tool_output = Column(JSON, nullable=True)
    error_msg = Column(String, nullable=True)
    
    sandbox_id = Column(String, nullable=True)
    execution_time_ms = Column(Integer, nullable=True)
    
    # Relationships
    chat = relationship("Chat", back_populates="tool_calls")


class SandboxFile(Base):
    """Files created by Claude in sandbox and saved to persistent storage"""
    __tablename__ = 'sandbox_files'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    chat_id = Column(String, ForeignKey('chats.id'), nullable=False)
    
    # File identification
    filepath = Column(String, nullable=False)      # "calculator/main.py"
    filename = Column(String, nullable=False)      # "main.py"
    directory = Column(String, nullable=True)      # "calculator/" or None
    
    # File content
    content = Column(String, nullable=False)       # File contents (TEXT or base64 for binary)
    description = Column(String, nullable=True)    # Optional description from Claude
    is_binary = Column(Integer, default=0)         # 1 if content is base64-encoded binary
    
    # Metadata
    file_type = Column(String, nullable=True)      # "python", "json", etc
    size_bytes = Column(Integer, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    chat = relationship("Chat", back_populates="sandbox_files")


class Database:
    """Database manager"""
    
    def __init__(self, db_path: str):
        # Create directory if it doesn't exist
        import os
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        self.engine = create_engine(f'sqlite:///{db_path}')
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
    
    def get_session(self) -> Session:
        """Get a new database session"""
        return self.SessionLocal()
    
    # Chat operations
    def create_chat(self, title: str) -> Chat:
        """Create a new chat"""
        with self.get_session() as session:
            chat = Chat(title=title)
            session.add(chat)
            session.commit()
            session.refresh(chat)
            return chat
    
    def get_chat(self, chat_id: str) -> Optional[Chat]:
        """Get a chat by ID"""
        with self.get_session() as session:
            return session.query(Chat).filter(Chat.id == chat_id).first()
    
    def get_all_chats(self) -> List[Chat]:
        """Get all chats ordered by last updated"""
        with self.get_session() as session:
            chats = session.query(Chat).order_by(Chat.last_updated.desc()).all()
            # Detach from session
            session.expunge_all()
            return chats
    
    def update_chat(self, chat_id: str, **kwargs) -> None:
        """Update chat attributes"""
        with self.get_session() as session:
            chat = session.query(Chat).filter(Chat.id == chat_id).first()
            if chat:
                for key, value in kwargs.items():
                    setattr(chat, key, value)
                chat.last_updated = datetime.utcnow()
                session.commit()
    
    def delete_chat(self, chat_id: str) -> None:
        """Delete a chat and all associated data"""
        with self.get_session() as session:
            chat = session.query(Chat).filter(Chat.id == chat_id).first()
            if chat:
                session.delete(chat)
                session.commit()
    
    # Message operations
    def add_message(self, chat_id: str, role: str, content: Any, token_count: int = 0) -> Message:
        """Add a message to a chat"""
        with self.get_session() as session:
            message = Message(
                chat_id=chat_id,
                role=role,
                content=content,
                token_count=token_count
            )
            session.add(message)
            
            # Update chat metadata
            chat = session.query(Chat).filter(Chat.id == chat_id).first()
            if chat:
                chat.message_count += 1
                chat.total_tokens += token_count
                chat.last_updated = datetime.utcnow()
            
            session.commit()
            session.refresh(message)
            return message
    
    def get_messages(self, chat_id: str) -> List[Message]:
        """Get all messages for a chat"""
        with self.get_session() as session:
            messages = session.query(Message)\
                .filter(Message.chat_id == chat_id)\
                .order_by(Message.timestamp)\
                .all()
            session.expunge_all()
            return messages
    
    # File operations
    def add_file(self, chat_id: str, filename: str, file_path: str, 
                 file_type: str, size_bytes: int, token_estimate: int) -> FileAttachment:
        """Add a file attachment"""
        with self.get_session() as session:
            file_attachment = FileAttachment(
                chat_id=chat_id,
                filename=filename,
                file_path=file_path,
                file_type=file_type,
                size_bytes=size_bytes,
                token_estimate=token_estimate
            )
            session.add(file_attachment)
            session.commit()
            session.refresh(file_attachment)
            return file_attachment
    
    def get_files(self, chat_id: str) -> List[FileAttachment]:
        """Get all files for a chat"""
        with self.get_session() as session:
            files = session.query(FileAttachment)\
                .filter(FileAttachment.chat_id == chat_id)\
                .order_by(FileAttachment.uploaded_at)\
                .all()
            session.expunge_all()
            return files
    
    def update_file_context(self, file_id: str, in_context: bool) -> None:
        """Update whether a file is in context"""
        with self.get_session() as session:
            file_attachment = session.query(FileAttachment).filter(FileAttachment.id == file_id).first()
            if file_attachment:
                file_attachment.in_context = in_context
                session.commit()
    
    def delete_file(self, file_id: str) -> None:
        """Delete a file attachment"""
        with self.get_session() as session:
            file_attachment = session.query(FileAttachment).filter(FileAttachment.id == file_id).first()
            if file_attachment:
                session.delete(file_attachment)
                session.commit()
    
    # Thinking event operations
    def log_thinking(self, chat_id: str, thinking_text: str, signature: Optional[str] = None, 
                    message_id: Optional[str] = None, iteration: int = 0) -> int:
        """Log a thinking event, returns event_id"""
        with self.get_session() as session:
            event = ThinkingEvent(
                chat_id=chat_id,
                message_id=message_id,
                thinking_text=thinking_text,
                signature=signature,
                iteration=iteration
            )
            session.add(event)
            session.commit()
            session.refresh(event)
            print(f"[DB] Logged thinking event #{event.id} for chat {chat_id[:8]}, iteration {iteration}")
            return event.id
    
    def get_thinking_events(self, chat_id: str, message_id: Optional[str] = None) -> List[ThinkingEvent]:
        """Get thinking events for a chat, optionally filtered by message_id"""
        with self.get_session() as session:
            query = session.query(ThinkingEvent).filter(ThinkingEvent.chat_id == chat_id)
            if message_id:
                query = query.filter(ThinkingEvent.message_id == message_id)
            events = query.order_by(ThinkingEvent.timestamp).all()
            session.expunge_all()
            return events
    
    # Tool call operations
    def log_tool_call(self, chat_id: str, tool_name: str, tool_input: Dict[str, Any],
                     message_id: Optional[str] = None, iteration: int = 0) -> int:
        """Log start of a tool call, returns event_id"""
        with self.get_session() as session:
            tool_call = ToolCall(
                chat_id=chat_id,
                message_id=message_id,
                tool_name=tool_name,
                tool_input=tool_input,
                iteration=iteration,
                status='pending'
            )
            session.add(tool_call)
            session.commit()
            session.refresh(tool_call)
            print(f"[DB] Logged tool_call #{tool_call.id}: {tool_name} (iteration {iteration})")
            return tool_call.id
    
    def update_tool_call(self, event_id: int, status: str, tool_output: Optional[Dict[str, Any]] = None,
                        error_msg: Optional[str] = None, sandbox_id: Optional[str] = None,
                        execution_time_ms: Optional[float] = None) -> None:
        """Update a tool call with result"""
        with self.get_session() as session:
            tool_call = session.query(ToolCall).filter(ToolCall.id == event_id).first()
            if tool_call:
                tool_call.status = status
                if tool_output:
                    tool_call.tool_output = tool_output
                if error_msg:
                    tool_call.error_msg = error_msg
                if sandbox_id:
                    tool_call.sandbox_id = sandbox_id
                if execution_time_ms is not None:
                    tool_call.execution_time_ms = execution_time_ms
                session.commit()
                print(f"[DB] Updated tool_call #{event_id}: status={status}")
    
    def get_tool_calls(self, chat_id: str, message_id: Optional[str] = None, 
                      status: Optional[str] = None) -> List[ToolCall]:
        """Get tool calls for a chat, optionally filtered"""
        with self.get_session() as session:
            query = session.query(ToolCall).filter(ToolCall.chat_id == chat_id)
            if message_id:
                query = query.filter(ToolCall.message_id == message_id)
            if status:
                query = query.filter(ToolCall.status == status)
            calls = query.order_by(ToolCall.timestamp).all()
            session.expunge_all()
            return calls
    
    def get_execution_events(self, chat_id: str, message_id: Optional[str] = None):
        """Get all execution events (thinking + tool calls) for a chat/message"""
        thinking = self.get_thinking_events(chat_id, message_id)
        tools = self.get_tool_calls(chat_id, message_id)
        
        # Combine and sort by timestamp
        events = []
        for t in thinking:
            events.append({
                'type': 'thinking',
                'timestamp': t.timestamp,
                'iteration': t.iteration,
                'thinking_text': t.thinking_text,
                'signature': t.signature,
                'id': t.id
            })
        
        for tc in tools:
            events.append({
                'type': 'tool_call',
                'timestamp': tc.timestamp,
                'iteration': tc.iteration,
                'tool_name': tc.tool_name,
                'tool_input': tc.tool_input,
                'status': tc.status,
                'tool_output': tc.tool_output,
                'error_msg': tc.error_msg,
                'sandbox_id': tc.sandbox_id,
                'execution_time_ms': tc.execution_time_ms,
                'id': tc.id
            })
        
        # Sort by timestamp
        events.sort(key=lambda x: x['timestamp'])
        return events
    
    def add_sandbox_file(self, chat_id: str, filepath: str, filename: str, 
                        directory: Optional[str], content: str, description: Optional[str],
                        file_type: Optional[str], size_bytes: int,
                        is_binary: bool = False) -> SandboxFile:
        """
        Add a new sandbox file to persistent storage.
        
        Args:
            chat_id: Chat ID
            filepath: Full file path (e.g., "calculator/main.py")
            filename: Just the filename (e.g., "main.py")
            directory: Directory path (e.g., "calculator/") or None
            content: File contents (text or base64-encoded for binary)
            description: Optional description from Claude
            file_type: File type (e.g., "python", "json")
            size_bytes: File size in bytes
            is_binary: True if content is base64-encoded binary
        
        Returns:
            Created SandboxFile object
        """
        with self.get_session() as session:
            sandbox_file = SandboxFile(
                chat_id=chat_id,
                filepath=filepath,
                filename=filename,
                directory=directory,
                content=content,
                description=description,
                file_type=file_type,
                size_bytes=size_bytes,
                is_binary=1 if is_binary else 0
            )
            session.add(sandbox_file)
            session.commit()
            session.refresh(sandbox_file)
            print(f"[DB] Added sandbox file: {filepath} ({size_bytes} bytes, {'binary' if is_binary else 'text'}) to chat {chat_id}")
            return sandbox_file
    
    def update_sandbox_file(self, file_id: str, content: str, size_bytes: int,
                           is_binary: bool = False) -> None:
        """
        Update an existing sandbox file's content.
        
        Args:
            file_id: File ID to update
            content: New file contents (text or base64 for binary)
            size_bytes: New file size
            is_binary: True if content is base64-encoded binary
        """
        with self.get_session() as session:
            sandbox_file = session.query(SandboxFile).filter(SandboxFile.id == file_id).first()
            if sandbox_file:
                sandbox_file.content = content
                sandbox_file.size_bytes = size_bytes
                sandbox_file.is_binary = 1 if is_binary else 0
                sandbox_file.updated_at = datetime.utcnow()
                session.commit()
                print(f"[DB] Updated sandbox file: {sandbox_file.filepath} ({size_bytes} bytes)")
            else:
                print(f"[DB] Warning: Sandbox file {file_id} not found for update")
    
    def get_sandbox_file(self, chat_id: str, filepath: str) -> Optional[SandboxFile]:
        """
        Get a single sandbox file by chat ID and filepath.
        
        Args:
            chat_id: Chat ID
            filepath: File path to search for
        
        Returns:
            SandboxFile object if found, None otherwise
        """
        with self.get_session() as session:
            sandbox_file = session.query(SandboxFile)\
                .filter(SandboxFile.chat_id == chat_id)\
                .filter(SandboxFile.filepath == filepath)\
                .first()
            if sandbox_file:
                session.expunge(sandbox_file)
                print(f"[DB] Found sandbox file: {filepath}")
            else:
                print(f"[DB] Sandbox file not found: {filepath}")
            return sandbox_file
    
    def get_sandbox_files(self, chat_id: str) -> List[SandboxFile]:
        """
        Get all sandbox files for a chat.
        
        Args:
            chat_id: Chat ID
        
        Returns:
            List of SandboxFile objects ordered by filepath
        """
        with self.get_session() as session:
            files = session.query(SandboxFile)\
                .filter(SandboxFile.chat_id == chat_id)\
                .order_by(SandboxFile.filepath)\
                .all()
            session.expunge_all()
            print(f"[DB] Retrieved {len(files)} sandbox files for chat {chat_id}")
            return files