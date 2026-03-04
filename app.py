"""
Claude Chatbot POC - Main Streamlit Application

CODE-FIRST improvements:
1. Uploaded files are auto-copied to sandbox (/home/user/uploads/)
2. Data files (csv, xlsx, json, etc.) send a sandbox_file_ref instead of raw content
3. New execute_bash tool support in UI
4. Excel/xls/xlsx support added
"""

import os
import yaml
import streamlit as st
from datetime import datetime
from dotenv import load_dotenv

from src.database import Database
from src.llm_client import LLMClient
from src.file_handler import FileHandler
from src.code_executor import CodeExecutor
from src.mcp_client import MCPTrinoClient
from src.trino_tool_handler import TrinoToolHandler
from src.utils import (
    estimate_tokens, estimate_file_tokens, calculate_cost,
    format_token_count, format_cost, get_file_type, truncate_text,
    get_context_messages, estimate_message_tokens
)

# Load environment variables
load_dotenv()

# Load configuration
with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)


# ---- File types that should be processed by CODE, not sent inline ----
# These get uploaded to sandbox and referenced, not embedded in the message
CODE_PROCESSABLE_TYPES = {
    'csv', 'xlsx', 'xls', 'json', 'xml', 'tsv',
    'parquet', 'sql', 'db', 'sqlite',
}

# These are small enough / appropriate to embed as text in the message
INLINE_TEXT_TYPES = {
    'txt', 'md', 'py', 'js', 'ts', 'html', 'css', 'yaml', 'yml',
    'sh', 'bash', 'toml', 'ini', 'cfg', 'conf', 'env',
}

# Size threshold: files larger than this are always routed through sandbox
INLINE_SIZE_THRESHOLD = 50_000  # 50KB — above this, use code even for text files

# MIME type mapping for downloads
MIME_TYPES = {
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'xls': 'application/vnd.ms-excel',
    'pdf': 'application/pdf',
    'png': 'image/png',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'gif': 'image/gif',
    'zip': 'application/zip',
    'tar': 'application/x-tar',
    'gz': 'application/gzip',
    'parquet': 'application/octet-stream',
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'csv': 'text/csv',
    'json': 'application/json',
    'sql': 'text/plain',
    'py': 'text/x-python',
    'txt': 'text/plain',
    'md': 'text/markdown',
    'html': 'text/html',
}

def _get_mime_type(filename: str) -> str:
    """Get MIME type for a filename based on extension."""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return MIME_TYPES.get(ext, 'application/octet-stream')


def code_block_with_copy(code: str, language: str = 'python', label: str = None):
    """Display a code block with a copy button (safe unique keys)."""
    import hashlib
    import uuid

    content_hash = hashlib.md5(code.encode()).hexdigest()[:8]
    unique_suffix = uuid.uuid4().hex[:6]
    button_key = f"copy_{content_hash}_{unique_suffix}"

    col1, col2 = st.columns([12, 1])

    with col1:
        if label:
            st.caption(label)
        st.code(code, language=language)

    with col2:
        if st.button("📋", key=button_key, help="Copy to clipboard"):
            escaped_code = code.replace('`', r'\`')
            st.components.v1.html(
                f"""
                <script>
                    const code = `{escaped_code}`;
                    navigator.clipboard.writeText(code).then(
                        () => console.log("Copied"),
                        err => console.error("Copy failed", err)
                    );
                </script>
                """,
                height=0,
            )
            st.toast("📋 Copied to clipboard!", icon="✅")


def init_session_state():
    """Initialize Streamlit session state"""
    if 'db' not in st.session_state:
        st.session_state.db = Database(config['database']['path'])
    
    if 'file_handler' not in st.session_state:
        st.session_state.file_handler = FileHandler(config['storage']['uploads_dir'])
    
    if 'code_executor' not in st.session_state and config['code_execution']['enabled']:
        try:
            st.session_state.code_executor = CodeExecutor(
                timeout_seconds=config['code_execution']['timeout_seconds'],
                db=st.session_state.db
            )
        except ValueError as e:
            st.warning(f"⚠️ Code execution disabled: {str(e)}")
            st.session_state.code_executor = None
    
    if 'llm_client' not in st.session_state:
        api_key = os.getenv('LLM_API_KEY')
        base_url = os.getenv('LLM_BASE_URL')
        if not api_key:
            st.error("LLM_API_KEY not found in environment variables!")
            st.stop()
        
        st.session_state.llm_client = LLMClient(
            api_key=api_key,
            model=config['llm']['model'],
            max_tokens=config['llm']['max_tokens'],
            base_url=base_url,
            endpoint_name=config['llm']['endpoint_name'],
            db=st.session_state.db
        )

    # ---- Trino MCP integration ----
    if 'mcp_client' not in st.session_state:
        if config.get('trino', {}).get('enabled', False):
            try:
                st.session_state.mcp_client = MCPTrinoClient(
                    mcp_url=config['trino']['mcp_url'],
                    default_username=config['trino']['default_user']
                )
                st.session_state.trino_handler = TrinoToolHandler(
                    mcp_client=st.session_state.mcp_client,
                    code_executor=st.session_state.code_executor,
                    db=st.session_state.db
                )
                print("[APP] Trino MCP integration enabled")
            except Exception as e:
                st.warning(f"⚠️ Trino integration disabled: {str(e)}")
                st.session_state.mcp_client = None
                st.session_state.trino_handler = None
        else:
            st.session_state.mcp_client = None
            st.session_state.trino_handler = None

    if 'current_chat_id' not in st.session_state:
        st.session_state.current_chat_id = None
    
    if 'uploaded_files_temp' not in st.session_state:
        st.session_state.uploaded_files_temp = []


def create_new_chat():
    """Create a new chat"""
    chat = st.session_state.db.create_chat(title="New Chat")
    st.session_state.current_chat_id = chat.id
    st.session_state.uploaded_files_temp = []
    st.rerun()


def switch_chat(chat_id: str):
    """Switch to a different chat"""
    st.session_state.current_chat_id = chat_id
    st.session_state.uploaded_files_temp = []
    st.rerun()


def delete_current_chat():
    """Delete the current chat"""
    if st.session_state.current_chat_id:
        chat_id = st.session_state.current_chat_id
        
        if st.session_state.code_executor:
            st.session_state.code_executor.close_sandbox(chat_id)
        
        st.session_state.file_handler.delete_chat_files(chat_id)
        st.session_state.db.delete_chat(chat_id)
        
        chats = st.session_state.db.get_all_chats()
        if chats:
            st.session_state.current_chat_id = chats[0].id
        else:
            create_new_chat()
        
        st.rerun()


def _upload_file_to_sandbox(file_path: str, filename: str) -> dict:
    """
    Upload a file to the sandbox workspace.
    Returns upload result dict with sandbox_path.
    """
    if not st.session_state.code_executor:
        return {"success": False, "error": "Code executor not available"}
    
    chat = st.session_state.db.get_chat(st.session_state.current_chat_id)
    sandbox_id = chat.sandbox_id if chat else None
    
    result = st.session_state.code_executor.upload_to_sandbox(
        st.session_state.current_chat_id,
        sandbox_id,
        file_path,
        filename
    )
    
    # Update sandbox_id if it changed
    if result.get('sandbox_id') and result['sandbox_id'] != sandbox_id:
        st.session_state.db.update_chat(
            st.session_state.current_chat_id,
            sandbox_id=result['sandbox_id']
        )
    
    return result


def _should_use_sandbox(file_type: str, file_size: int) -> bool:
    """
    Determine if a file should be processed via sandbox code rather than
    embedded inline in the message.
    """
    # Data files always go through sandbox
    if file_type in CODE_PROCESSABLE_TYPES:
        return True
    
    # Large text files go through sandbox
    if file_size > INLINE_SIZE_THRESHOLD:
        return True
    
    # PDFs and images: keep as-is (the model can see images, PDFs get base64'd)
    # Small text files: embed inline (model can read them directly)
    return False


def render_sidebar():
    """Render the sidebar with chat list"""
    with st.sidebar:
        st.title("💬 Chats")
        
        if st.button("➕ New Chat", use_container_width=True):
            create_new_chat()
        
        debug_mode = st.checkbox("🐛 Debug Mode", value=False)
        
        st.divider()
        
        chats = st.session_state.db.get_all_chats()
        
        if not chats:
            st.info("No chats yet. Create one!")
            return
        
        for chat in chats:
            is_active = chat.id == st.session_state.current_chat_id
            
            button_label = f"{'🟢' if is_active else '⚪'} {truncate_text(chat.title, 25)}"
            
            if st.button(button_label, key=f"chat_{chat.id}", use_container_width=True):
                if not is_active:
                    switch_chat(chat.id)
            
            if is_active:
                st.caption(f"💬 {chat.message_count} msgs | 🕒 {chat.last_updated.strftime('%m/%d %H:%M')}")
                
                with st.expander("📁 Project Files", expanded=False):
                    try:
                        saved_files = st.session_state.db.get_sandbox_files(chat.id)
                        
                        if saved_files:
                            file_tree = {}
                            for file in saved_files:
                                dir_name = file.directory or ''
                                if dir_name not in file_tree:
                                    file_tree[dir_name] = []
                                file_tree[dir_name].append(file)
                            
                            for directory in sorted(file_tree.keys()):
                                dir_files = file_tree[directory]
                                if directory:
                                    st.markdown(f"**📂 {directory}**")
                                
                                for file in sorted(dir_files, key=lambda f: f.filename):
                                    col1, col2, col3 = st.columns([3, 1, 1])
                                    
                                    with col1:
                                        indent = "    " if directory else "  "
                                        st.text(f"{indent}📄 {file.filename}")
                                    
                                    with col2:
                                        if st.button("👁️", key=f"view_{file.id}", help="Preview file"):
                                            st.session_state.preview_file = file
                                    
                                    with col3:
                                        # Decode binary files for download
                                        if getattr(file, 'is_binary', 0):
                                            import base64
                                            try:
                                                dl_data = base64.b64decode(file.content)
                                            except Exception:
                                                dl_data = file.content
                                            dl_mime = _get_mime_type(file.filename)
                                        else:
                                            dl_data = file.content
                                            dl_mime = "text/plain"
                                        
                                        st.download_button(
                                            "⬇️",
                                            data=dl_data,
                                            file_name=file.filename,
                                            mime=dl_mime,
                                            key=f"dl_{file.id}",
                                            help=f"Download {file.filename}"
                                        )
                            
                            if hasattr(st.session_state, 'preview_file') and st.session_state.preview_file:
                                preview_file = st.session_state.preview_file
                                with st.container():
                                    st.markdown("---")
                                    st.markdown(f"### 📄 {preview_file.filename}")
                                    if preview_file.description:
                                        st.caption(preview_file.description)
                                    st.caption(f"Type: {preview_file.file_type} | Size: {preview_file.size_bytes} bytes")
                                    if getattr(preview_file, 'is_binary', 0):
                                        st.info("📦 Binary file — use the download button to access this file.")
                                    else:
                                        code_block_with_copy(preview_file.content, language=preview_file.file_type)
                                    if st.button("Close Preview", key="close_preview"):
                                        st.session_state.preview_file = None
                                        st.rerun()
                            
                            total_size = sum(f.size_bytes or 0 for f in saved_files)
                            st.caption(f"**{len(saved_files)} file(s)** | Total: {total_size:,} bytes")
                        
                        else:
                            st.caption("No saved files yet")
                    
                    except Exception as e:
                        st.caption(f"⚠️ Error loading files")
                        print(f"[APP] Error loading sidebar files: {str(e)}")
                
                if debug_mode:
                    with st.expander("🐛 Debug Info", expanded=True):
                        if chat.sandbox_id:
                            st.text(f"Sandbox: {chat.sandbox_id[:12]}...")
                        else:
                            st.text("Sandbox: None")
                        
                        tool_calls = st.session_state.db.get_tool_calls(chat.id)
                        if tool_calls:
                            st.write(f"**Tool Calls:** {len(tool_calls)}")
                            for tc in tool_calls[-5:]:
                                status_icon = "✅" if tc.status == 'success' else "❌"
                                st.text(f"{status_icon} {tc.tool_name} ({tc.execution_time_ms:.0f}ms)")


def render_file_upload():
    """Render file upload section"""
    st.subheader("📎 Upload Files")
    
    uploaded_files = st.file_uploader(
        "Choose files",
        accept_multiple_files=True,
        type=config['files']['allowed_extensions'],
        key=f"file_uploader_{st.session_state.current_chat_id}"
    )
    
    if uploaded_files:
        for file in uploaded_files:
            if file.name not in [f['name'] for f in st.session_state.uploaded_files_temp]:
                file_type = get_file_type(file.name)
                
                # Save file to disk (host)
                file_path = st.session_state.file_handler.save_file(
                    file.read(),
                    file.name,
                    st.session_state.current_chat_id
                )
                
                token_estimate = estimate_file_tokens(
                    file_path,
                    file_type,
                    config['context']['token_estimation_ratio']
                )
                
                # Add to database
                db_file = st.session_state.db.add_file(
                    chat_id=st.session_state.current_chat_id,
                    filename=file.name,
                    file_path=file_path,
                    file_type=file_type,
                    size_bytes=file.size,
                    token_estimate=token_estimate
                )
                
                # ---- CODE-FIRST: Auto-upload to sandbox ----
                sandbox_result = _upload_file_to_sandbox(file_path, file.name)
                sandbox_path = sandbox_result.get('sandbox_path', '')
                
                if sandbox_result.get('success'):
                    upload_status = f"✅ In sandbox: {sandbox_path}"
                else:
                    upload_status = f"⚠️ Sandbox upload failed"
                
                st.session_state.uploaded_files_temp.append({
                    'id': db_file.id,
                    'name': file.name,
                    'size': file.size,
                    'type': file_type,
                    'tokens': token_estimate,
                    'sandbox_path': sandbox_path,
                    'in_sandbox': sandbox_result.get('success', False)
                })
                
                # Show status
                use_sandbox = _should_use_sandbox(file_type, file.size)
                if use_sandbox:
                    st.success(f"📂 {file.name} → sandbox (will be processed with code)")
                else:
                    st.info(f"📎 {file.name} → message context")
    
    # Display uploaded files
    files = st.session_state.db.get_files(st.session_state.current_chat_id)
    
    if files:
        st.write("**Files in this chat:**")
        for file in files:
            col1, col2, col3 = st.columns([3, 1, 1])
            
            with col1:
                use_sandbox = _should_use_sandbox(file.file_type, file.size_bytes)
                icon = "📂" if use_sandbox else "📎"
                status = "✅" if file.in_context else "⚪"
                st.write(f"{status} {icon} {truncate_text(file.filename, 30)}")
            
            with col2:
                if use_sandbox:
                    st.caption("code mode")
                else:
                    st.caption(f"{format_token_count(file.token_estimate)} tokens")
            
            with col3:
                if st.button("🗑️", key=f"delete_file_{file.id}"):
                    st.session_state.file_handler.delete_file(file.file_path)
                    st.session_state.db.delete_file(file.id)
                    st.rerun()


def render_messages():
    """Render chat messages"""
    messages = st.session_state.db.get_messages(st.session_state.current_chat_id)
    
    for msg in messages:
        # Skip tool_result messages
        if msg.role == "user" and isinstance(msg.content, list):
            if any(block.get('type') == 'tool_result' for block in msg.content):
                continue
        
        with st.chat_message(msg.role):
            st.caption(f"🕐 {msg.timestamp.strftime('%I:%M %p')}")
            
            content = msg.content
            
            if msg.role == "user":
                if isinstance(content, list):
                    text_blocks = []
                    file_count = 0
                    
                    for block in content:
                        block_type = block.get('type')
                        
                        if block_type == 'document':
                            file_count += 1
                            st.caption("📄 PDF Document")
                        elif block_type == 'image':
                            file_count += 1
                            st.caption("🖼️ Image")
                        elif block_type == 'sandbox_file_ref':
                            file_count += 1
                            st.caption(f"📂 {block.get('filename', 'file')} (in sandbox)")
                        elif block_type == 'text':
                            text_blocks.append(block.get('text', ''))
                    
                    if text_blocks:
                        st.markdown(text_blocks[-1])
                
                elif isinstance(content, str):
                    st.markdown(content)
            
            elif msg.role == "assistant":
                if isinstance(content, list):
                    for block in content:
                        if block.get('type') == 'thinking':
                            if config['code_execution']['ui']['show_thinking']:
                                with st.expander("🤔 View Thinking Process"):
                                    st.markdown(block.get('thinking', ''))
                        
                        elif block.get('type') == 'tool_use':
                            if config['code_execution']['ui']['show_code_attempts']:
                                with st.expander("⚙️ Code Execution", expanded=True):
                                    code = block['input'].get('code', '')
                                    if code:
                                        code_block_with_copy(code, language='python')
                        
                        elif block.get('type') == 'tool_result_display':
                            tool_name = block.get('tool_name')
                            tool_input = block.get('tool_input', {})
                            result = block.get('result', {})
                            iteration = block.get('iteration', 0)
                            
                            _render_tool_result_display(tool_name, tool_input, result, iteration)
                        
                        elif block.get('type') == 'text':
                            st.markdown(block.get('text', ''))
                
                elif isinstance(content, str):
                    st.markdown(content)


def _render_tool_result_display(tool_name, tool_input, result, iteration):
    """Render a persisted tool result display block."""
    if tool_name == 'create_file':
        with st.expander(f"📝 Created file: {tool_input.get('path', '')}", expanded=False):
            content = tool_input.get('content', '')
            if content:
                code_block_with_copy(content, language='python')
            if result.get('success'):
                st.success("✅ File created successfully")
            else:
                st.error(f"❌ Error: {result.get('error')}")
    
    elif tool_name == 'read_file':
        with st.expander(f"📖 Read file: {tool_input.get('path', '')}", expanded=False):
            if result.get('success'):
                content = result.get('content', '')
                if content:
                    code_block_with_copy(content, language='python')
            else:
                st.error(f"❌ Error: {result.get('error')}")
    
    elif tool_name == 'list_files':
        directory = tool_input.get('directory', '/home/user')
        with st.expander(f"📂 Listed files in {directory}", expanded=False):
            if result.get('success'):
                files = result.get('files', [])
                if files:
                    for f in files:
                        st.text(f)
                else:
                    st.info("No files found")
            else:
                st.error(f"❌ Error: {result.get('error')}")
    
    elif tool_name == 'execute_python':
        code = tool_input.get('code')
        file_path = tool_input.get('file_path')
        
        title = f"▶️ Executed: {file_path}" if file_path else f"▶️ Executed code (Iteration {iteration})"
        
        with st.expander(title, expanded=True):
            if code:
                code_block_with_copy(code, language='python', label="📝 Code:")
            
            if result.get('success'):
                st.success("✅ Success")
                output = result.get('output', '(no output)')
                if output and output != '(no output)':
                    code_block_with_copy(output, language='text', label="💾 Output:")
                else:
                    st.text(output)
            else:
                st.error("❌ Error")
                error = result.get('error', 'Unknown error')
                if error:
                    code_block_with_copy(error, language='text', label="⚠️ Error:")
    
    elif tool_name == 'execute_bash':
        command = tool_input.get('command', '')
        with st.expander(f"🖥️ Bash: {truncate_text(command, 50)}", expanded=True):
            st.code(command, language='bash')
            
            if result.get('success'):
                st.success("✅ Success")
                output = result.get('output', '(no output)')
                if output and output not in ('(no output)', 'Command executed successfully (no output).'):
                    code_block_with_copy(output, language='text', label="💾 Output:")
            else:
                st.error("❌ Error")
                error = result.get('error', 'Unknown error')
                if error:
                    code_block_with_copy(error, language='text', label="⚠️ Error:")
    
    # ---- Trino Lakehouse Tool Results ----
    elif tool_name == 'health_check':
        with st.expander("🔌 Trino Health Check", expanded=False):
            if result.get('success'):
                st.success(f"✅ {result.get('output', 'Connected')}")
            else:
                st.error(f"❌ {result.get('error', 'Health check failed')}")

    elif tool_name == 'list_schemas':
        with st.expander("📂 Listed schemas", expanded=False):
            if result.get('success'):
                st.text(result.get('output', ''))
            else:
                st.error(f"❌ {result.get('error', 'Failed to list schemas')}")

    elif tool_name == 'list_tables':
        schema = tool_input.get('schema', '?')
        with st.expander(f"📋 Tables in {schema}", expanded=False):
            if result.get('success'):
                st.text(result.get('output', ''))
            else:
                st.error(f"❌ {result.get('error', 'Failed to list tables')}")

    elif tool_name == 'describe_table':
        table = tool_input.get('table', '?')
        schema = tool_input.get('schema', '?')
        with st.expander(f"🔍 Describe {schema}.{table}", expanded=False):
            if result.get('success'):
                st.code(result.get('output', ''), language='text')
            else:
                st.error(f"❌ {result.get('error', 'Failed to describe table')}")

    elif tool_name == 'run_query':
        sql = tool_input.get('sql', '')
        sql_preview = (sql[:80] + '...') if len(sql) > 80 else sql
        with st.expander(f"🔎 Query: {sql_preview}", expanded=True):
            code_block_with_copy(sql, language='sql', label="SQL:")
            if result.get('success'):
                st.success("✅ Query executed")
                output = result.get('output', '')
                if output:
                    # Show the summary (row count, preview, file path)
                    st.text(output[:3000])
            else:
                st.error(f"❌ {result.get('error', 'Query failed')}")

    elif tool_name == 'save_files':
        files_list = tool_input.get('files', [])
        file_results = result.get('files', [])
        saved_count = result.get('saved', 0)
        failed_count = result.get('failed', 0)
        label = f"💾 Saved {saved_count} file(s)" if saved_count else "💾 Save files"
        if failed_count:
            label += f" ({failed_count} failed)"
        with st.expander(label, expanded=False):
            for fr in file_results:
                if fr.get('success'):
                    st.success(f"✅ {fr.get('filename', fr.get('filepath', '?'))} — {fr.get('action', 'saved')} ({fr.get('size', 0)} bytes)")
                else:
                    st.error(f"❌ {fr.get('filepath', '?')} — {fr.get('error', 'Unknown error')}")


def send_message(user_input: str):
    """Send a message with code-first file handling"""
    if not user_input.strip():
        return
    
    # Get files in context
    files = st.session_state.db.get_files(st.session_state.current_chat_id)
    files_in_context = [f for f in files if f.in_context]
    
    # Build message content with code-first routing
    message_content = []
    
    for file in files_in_context:
        file_type = file.file_type
        file_size = file.size_bytes
        
        if _should_use_sandbox(file_type, file_size):
            # ---- CODE-FIRST: Reference file in sandbox, don't embed ----
            # Find sandbox path from temp uploads
            sandbox_path = f"/home/user/uploads/{file.filename}"
            
            message_content.append({
                "type": "sandbox_file_ref",
                "filename": file.filename,
                "sandbox_path": sandbox_path,
                "file_type": file_type,
                "size_bytes": file_size
            })
        else:
            # Small text files, PDFs, images — embed as before
            claude_format = st.session_state.file_handler.convert_to_claude_format(
                file.file_path,
                file.file_type
            )
            if claude_format:
                message_content.append(claude_format)
    
    # Add user text
    message_content.append({
        "type": "text",
        "text": user_input
    })
    
    # Display user message IMMEDIATELY
    with st.chat_message("user"):
        for file in files_in_context:
            use_sandbox = _should_use_sandbox(file.file_type, file.size_bytes)
            if file.file_type == 'pdf':
                st.caption(f"📄 {file.filename}")
            elif file.file_type in ['png', 'jpg', 'jpeg', 'webp']:
                st.caption(f"🖼️ {file.filename}")
            elif use_sandbox:
                st.caption(f"📂 {file.filename} (sandbox)")
            else:
                st.caption(f"📎 {file.filename}")
        
        st.markdown(user_input)
    
    # Save user message
    user_msg_tokens = estimate_message_tokens(
        message_content,
        config['context']['token_estimation_ratio']
    )
    
    user_message = st.session_state.db.add_message(
        chat_id=st.session_state.current_chat_id,
        role="user",
        content=message_content,
        token_count=user_msg_tokens
    )
    message_id = user_message.id
    
    print(f"[APP] User message saved with ID: {message_id}")
    
    # Get conversation history
    all_messages = st.session_state.db.get_messages(st.session_state.current_chat_id)
    
    claude_messages = []
    for msg in all_messages[:-1]:
        if msg.role == "user" and isinstance(msg.content, list):
            if any(block.get('type') == 'tool_result' for block in msg.content):
                continue
        
        content = msg.content
        if isinstance(content, list):
            content = [block for block in content if block.get('type') != 'tool_result_display']
        
        claude_messages.append({
            "role": msg.role,
            "content": content
        })
    
    # Apply context window limit
    claude_messages = get_context_messages(
        claude_messages,
        config['context']['max_tokens'],
        config['context']['token_estimation_ratio']
    )
    
    # Add current message
    claude_messages.append({
        "role": "user",
        "content": message_content
    })
    
    # Call LLM API with tool support
    print(f"[APP] === Starting LLM call for chat {st.session_state.current_chat_id} ===")
    print(f"[APP] Message count in context: {len(claude_messages)}")
    
    with st.chat_message("assistant"):
        execution_placeholder = st.container()
        response_placeholder = st.container()
        
        response = None
        error_occurred = False
        error_message = None
        
        with st.spinner("🤔 Thinking..."):
            try:
                chat = st.session_state.db.get_chat(st.session_state.current_chat_id)
                current_sandbox_id = chat.sandbox_id if chat else None
                print(f"[APP] Current sandbox_id: {current_sandbox_id}")
                
                tool_executor = None
                iteration_counter = [0]
                
                if st.session_state.code_executor:
                    def tool_exec(tool_name: str, tool_input: dict):
                        """Execute a tool and update sandbox_id"""
                        nonlocal current_sandbox_id
                        current_iteration = iteration_counter[0]
                        iteration_counter[0] += 1
                        
                        print(f"[APP] Tool call #{current_iteration}: {tool_name}")
                        
                        try:
                            if tool_name == "create_file":
                                if 'path' not in tool_input:
                                    result = {"success": False, "error": "Missing required parameter: 'path'"}
                                elif 'content' not in tool_input:
                                    result = {"success": False, "error": "Missing required parameter: 'content'"}
                                else:
                                    result = st.session_state.code_executor.create_file(
                                        st.session_state.current_chat_id,
                                        current_sandbox_id,
                                        tool_input['path'],
                                        tool_input['content'],
                                        message_id=message_id,
                                        iteration=current_iteration
                                    )
                            
                            elif tool_name == "read_file":
                                if 'path' not in tool_input:
                                    result = {"success": False, "error": "Missing required parameter: 'path'"}
                                else:
                                    result = st.session_state.code_executor.read_file(
                                        st.session_state.current_chat_id,
                                        current_sandbox_id,
                                        tool_input['path'],
                                        message_id=message_id,
                                        iteration=current_iteration
                                    )
                            
                            elif tool_name == "list_files":
                                directory = tool_input.get('directory', '/home/user')
                                result = st.session_state.code_executor.list_files(
                                    st.session_state.current_chat_id,
                                    current_sandbox_id,
                                    directory,
                                    message_id=message_id,
                                    iteration=current_iteration
                                )
                            
                            elif tool_name == "execute_python":
                                code = tool_input.get('code')
                                file_path = tool_input.get('file_path')
                                if not code and not file_path:
                                    result = {"success": False, "error": "Must provide either 'code' or 'file_path'"}
                                else:
                                    result = st.session_state.code_executor.execute_python(
                                        st.session_state.current_chat_id,
                                        current_sandbox_id,
                                        code=code,
                                        file_path=file_path,
                                        message_id=message_id,
                                        iteration=current_iteration
                                    )
                            
                            elif tool_name == "execute_bash":
                                command = tool_input.get('command')
                                if not command:
                                    result = {"success": False, "error": "Missing required parameter: 'command'"}
                                else:
                                    result = st.session_state.code_executor.execute_bash(
                                        st.session_state.current_chat_id,
                                        current_sandbox_id,
                                        command,
                                        message_id=message_id,
                                        iteration=current_iteration
                                    )
                            
                            elif tool_name == "save_files":
                                files_list = tool_input.get('files', [])
                                if not files_list:
                                    result = {"success": False, "error": "Missing required parameter: 'files' array"}
                                else:
                                    result = st.session_state.code_executor.save_files(
                                        st.session_state.current_chat_id,
                                        current_sandbox_id,
                                        files_list,
                                        message_id=message_id,
                                        iteration=current_iteration
                                    )
                            
                            # ---- Trino Lakehouse Tools ----
                            elif tool_name in ('health_check', 'list_schemas', 'list_tables',
                                               'describe_table', 'run_query'):
                                if st.session_state.trino_handler:
                                    result = st.session_state.trino_handler.execute_tool(
                                        tool_name=tool_name,
                                        tool_input=tool_input,
                                        chat_id=st.session_state.current_chat_id,
                                        sandbox_id=current_sandbox_id,
                                        message_id=message_id,
                                        iteration=current_iteration
                                    )
                                else:
                                    result = {"success": False, "error": "Trino integration not configured. Check config.yaml trino.enabled and MCP server status."}
                            
                            else:
                                result = {"success": False, "error": f"Unknown tool: {tool_name}"}
                        
                        except Exception as e:
                            error_msg = f"Tool execution error: {str(e)}"
                            print(f"[APP] {error_msg}")
                            import traceback
                            traceback.print_exc()
                            result = {"success": False, "error": error_msg}
                        
                        # Update sandbox_id if it changed
                        if result.get('sandbox_id') and result['sandbox_id'] != current_sandbox_id:
                            current_sandbox_id = result['sandbox_id']
                            st.session_state.db.update_chat(
                                st.session_state.current_chat_id,
                                sandbox_id=current_sandbox_id
                            )
                        
                        print(f"[APP] Tool {tool_name} result: success={result.get('success')}")
                        return result
                    
                    tool_executor = tool_exec
                
                print(f"[APP] Calling LLM...")
                response = st.session_state.llm_client.send_message(
                    claude_messages,
                    chat_id=st.session_state.current_chat_id,
                    message_id=message_id,
                    tool_executor=tool_executor,
                    max_iterations=config['code_execution']['max_iterations']
                )
                print(f"[APP] LLM returned. tool_calls={len(response.get('tool_calls', []))}, stop={response.get('stop_reason')}")
            
            except Exception as e:
                error_occurred = True
                error_message = str(e)
                print(f"[APP] ❌ ERROR during LLM call: {error_message}")
                import traceback
                traceback.print_exc()
        
        # Display error if one occurred — and STOP (don't rerun, which would hide the error)
        if error_occurred:
            with response_placeholder:
                st.error(f"❌ Error: {error_message}")
            # Save a failed assistant message so the chat isn't broken
            st.session_state.db.add_message(
                chat_id=st.session_state.current_chat_id,
                role="assistant",
                content=[{"type": "text", "text": f"❌ Error: {error_message}"}],
                token_count=0
            )
            return  # DON'T rerun — let the user see the error
        
        # Display and save response
        if response:
            try:
                # Display tool calls
                with execution_placeholder:
                    if response.get('tool_calls'):
                        for tool_call in response['tool_calls']:
                            _render_tool_result_display(
                                tool_call['tool'],
                                tool_call['input'],
                                tool_call['result'],
                                tool_call['iteration']
                            )
                
                # Display response text
                with response_placeholder:
                    if response.get('max_iterations_reached'):
                        st.warning(f"⚠️ Maximum iterations ({config['code_execution']['max_iterations']}) reached.")
                    
                    for block in response.get('content', []):
                        if block.get('type') == 'thinking':
                            if config['code_execution']['ui']['show_thinking']:
                                with st.expander("🤔 View Thinking Process"):
                                    st.markdown(block.get('thinking', ''))
                        elif block.get('type') == 'text':
                            st.markdown(block.get('text', ''))
                
                # Display execution timeline
                events = st.session_state.db.get_execution_events(
                    st.session_state.current_chat_id,
                    message_id=message_id
                )
                
                if events:
                    with st.expander("📊 Execution Timeline", expanded=False):
                        for event in events:
                            if event['type'] == 'thinking':
                                st.write(f"**🤔 Thinking** (Iteration {event['iteration']})")
                                st.caption(f"{event['timestamp'].strftime('%H:%M:%S')}")
                            
                            elif event['type'] == 'tool_call':
                                status_icon = "✅" if event['status'] == 'success' else "❌"
                                tool_icon = {
                                    'create_file': '📝',
                                    'read_file': '📖',
                                    'list_files': '📂',
                                    'execute_python': '▶️',
                                    'execute_bash': '🖥️',
                                    'save_files': '💾',
                                    'health_check': '🔌',
                                    'list_schemas': '📂',
                                    'list_tables': '📋',
                                    'describe_table': '🔍',
                                    'run_query': '🔎',
                                }.get(event['tool_name'], '⚙️')
                                
                                exec_time = f" ({event['execution_time_ms']:.1f}ms)" if event['execution_time_ms'] else ""
                                st.write(f"{status_icon} {tool_icon} **{event['tool_name']}** (Iteration {event['iteration']}){exec_time}")
                        
                        st.divider()
                        st.caption(f"Total events: {len(events)}")
                
                # ---- Save assistant message ----
                assistant_tokens = response.get('usage', {}).get('output_tokens', 0)
                enriched_content = list(response.get('content', []))
                
                # Append tool result display blocks for persistence
                if response.get('tool_calls'):
                    for tool_call in response['tool_calls']:
                        result_block = {
                            "type": "tool_result_display",
                            "tool_use_id": None,
                            "tool_name": tool_call['tool'],
                            "tool_input": tool_call['input'],
                            "result": tool_call['result'],
                            "iteration": tool_call['iteration']
                        }
                        enriched_content.append(result_block)
                
                st.session_state.db.add_message(
                    chat_id=st.session_state.current_chat_id,
                    role="assistant",
                    content=enriched_content,
                    token_count=assistant_tokens
                )
                print(f"[APP] Assistant message saved with {len(enriched_content)} content blocks")
                
                # Update chat title if it's "New Chat"
                chat = st.session_state.db.get_chat(st.session_state.current_chat_id)
                if chat and chat.title == "New Chat":
                    title = st.session_state.llm_client.generate_title(user_input)
                    st.session_state.db.update_chat(
                        st.session_state.current_chat_id,
                        title=title
                    )
                
            except Exception as e:
                print(f"[APP] ❌ ERROR during response display/save: {str(e)}")
                import traceback
                traceback.print_exc()
                with response_placeholder:
                    st.error(f"❌ Error displaying response: {str(e)}")
                return  # Don't rerun on error
    
    st.rerun()


def render_chat_area():
    """Render the main chat area"""
    if not st.session_state.current_chat_id:
        st.info("👈 Create or select a chat to get started!")
        return
    
    chat = st.session_state.db.get_chat(st.session_state.current_chat_id)
    
    col1, col2 = st.columns([4, 1])
    with col1:
        st.title(chat.title)
    
    with col2:
        if st.button("🗑️ Delete Chat"):
            delete_current_chat()
    
    if config['costs']['show_cost_estimate']:
        total_cost = calculate_cost(
            chat.total_tokens // 2,
            chat.total_tokens // 2,
            config['costs']['input_cost_per_million'],
            config['costs']['output_cost_per_million']
        )
        st.caption(f"💰 Estimated cost: {format_cost(total_cost)} | 🎯 {format_token_count(chat.total_tokens)} tokens")
    
    st.divider()
    
    render_messages()
    
    user_input = st.chat_input("Type your message...")
    if user_input:
        send_message(user_input)


def main():
    """Main application"""
    st.set_page_config(
        page_title=config['ui']['page_title'],
        page_icon=config['ui']['page_icon'],
        layout="wide"
    )
    
    init_session_state()
    
    chats = st.session_state.db.get_all_chats()
    if not chats:
        create_new_chat()
    elif not st.session_state.current_chat_id:
        st.session_state.current_chat_id = chats[0].id
    
    render_sidebar()
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        render_chat_area()
    
    with col2:
        render_file_upload()


if __name__ == "__main__":
    main()