"""
Code execution using local Docker containers.
Drop-in replacement for E2B-based CodeExecutor.

CODE-FIRST improvements:
1. New execute_bash() method for shell commands / pip install
2. New upload_to_sandbox() method to copy uploaded files into workspace
3. Auto-creates /home/user/uploads/ directory in sandbox
"""

import os
import time
import tempfile
import shutil
from typing import Dict, Any, Optional, List
from pathlib import Path

import docker
from docker.errors import NotFound, APIError


class CodeExecutor:
    """Execute Python code in persistent Docker containers with multi-file support"""
    
    # Base image for Python execution (custom image with data libs pre-installed)
    DOCKER_IMAGE = "chatbot-sandbox:latest"
    
    # Container label for identification
    CONTAINER_LABEL = "chatbot-sandbox"
    
    def __init__(self, timeout_seconds: int = 60, db=None, workspace_base: str = None):
        self.timeout_seconds = timeout_seconds
        self.db = db
        
        # Set up workspace base directory
        if workspace_base:
            self.workspace_base = Path(workspace_base)
        else:
            self.workspace_base = Path(tempfile.gettempdir()) / "chatbot_workspaces"
        
        self.workspace_base.mkdir(parents=True, exist_ok=True)
        
        # Initialize Docker client
        try:
            self.docker_client = docker.from_env()
            self.docker_client.ping()
            print(f"[DOCKER] Connected to Docker daemon")
        except Exception as e:
            raise ValueError(f"Failed to connect to Docker: {str(e)}. Is Docker running?")
        
        # Pull image if not present
        self._ensure_image()
        
        # Cache of active containers by chat_id
        self._containers: Dict[str, docker.models.containers.Container] = {}
        
        # Map chat_id to workspace path
        self._workspaces: Dict[str, Path] = {}
    
    def _ensure_image(self):
        """Ensure the Python image is available locally."""
        try:
            self.docker_client.images.get(self.DOCKER_IMAGE)
            print(f"[DOCKER] Image {self.DOCKER_IMAGE} available")
        except NotFound:
            print(f"[DOCKER] Pulling image {self.DOCKER_IMAGE}...")
            self.docker_client.images.pull(self.DOCKER_IMAGE)
            print(f"[DOCKER] Image pulled successfully")
    
    def _get_workspace(self, chat_id: str) -> Path:
        """Get or create workspace directory for a chat."""
        if chat_id not in self._workspaces:
            workspace = self.workspace_base / chat_id
            workspace.mkdir(parents=True, exist_ok=True)
            # Core directories
            (workspace / "uploads").mkdir(parents=True, exist_ok=True)
            (workspace / "output").mkdir(parents=True, exist_ok=True)
            (workspace / "query_results").mkdir(parents=True, exist_ok=True)

            # Copy helper module into workspace — always overwrite to stay in sync with repo
            lib_dir = workspace / "lib"
            lib_dir.mkdir(parents=True, exist_ok=True)
            bundled_utils = Path(__file__).parent.parent / "sandbox_image" / "lib" / "lakehouse_utils.py"
            target_utils = lib_dir / "lakehouse_utils.py"
            if bundled_utils.exists():
                shutil.copy2(str(bundled_utils), str(target_utils))
                print(f"[DOCKER] Synced lakehouse_utils.py → {target_utils}")

            # Also create __init__.py so 'from lib.lakehouse_utils import ...' works
            init_file = lib_dir / "__init__.py"
            if not init_file.exists():
                init_file.write_text("", encoding="utf-8")

            self._workspaces[chat_id] = workspace
            print(f"[DOCKER] Created workspace: {workspace}")
        return self._workspaces[chat_id]

    def get_workspace(self, chat_id: str) -> Path:
        """Public accessor for workspace directory (used by TrinoToolHandler)."""
        return self._get_workspace(chat_id)
    
    def get_or_create_sandbox(self, chat_id: str, sandbox_id: Optional[str] = None):
        """Get existing container or create new one for a chat."""
        # Check if container already in memory and running
        if chat_id in self._containers:
            container = self._containers[chat_id]
            try:
                container.reload()
                if container.status == 'running':
                    return container
                else:
                    del self._containers[chat_id]
            except NotFound:
                del self._containers[chat_id]
        
        # Try to reconnect to existing container by ID
        if sandbox_id:
            try:
                container = self.docker_client.containers.get(sandbox_id)
                container.reload()
                if container.status == 'running':
                    self._containers[chat_id] = container
                    print(f"[DOCKER] Reconnected to container: {sandbox_id[:12]}")
                    return container
                elif container.status == 'exited':
                    container.start()
                    self._containers[chat_id] = container
                    print(f"[DOCKER] Restarted container: {sandbox_id[:12]}")
                    return container
            except NotFound:
                print(f"[DOCKER] Container {sandbox_id[:12]} not found, creating new one")
            except Exception as e:
                print(f"[DOCKER] Error reconnecting to {sandbox_id[:12]}: {e}")
        
        # Create new container
        workspace = self._get_workspace(chat_id)
        
        try:
            container = self.docker_client.containers.run(
                self.DOCKER_IMAGE,
                command="tail -f /dev/null",  # Keep container running
                detach=True,
                working_dir="/home/user",
                volumes={
                    str(workspace): {
                        'bind': '/home/user',
                        'mode': 'rw'
                    }
                },
                labels={
                    self.CONTAINER_LABEL: "true",
                    "chat_id": chat_id
                },
                mem_limit="512m",
                cpu_period=100000,
                cpu_quota=50000,
                network_mode="bridge",
                remove=False,
            )
            
            self._containers[chat_id] = container
            print(f"[DOCKER] Created new container: {container.id[:12]}")
            return container
            
        except Exception as e:
            raise ValueError(f"Failed to create container: {str(e)}")
    
    def close_sandbox(self, chat_id: str):
        """Close and cleanup container for a chat."""
        if chat_id in self._containers:
            try:
                container = self._containers[chat_id]
                container.stop(timeout=5)
                container.remove(force=True)
                print(f"[DOCKER] Removed container for chat {chat_id}")
            except Exception as e:
                print(f"[DOCKER] Error removing container: {e}")
            finally:
                del self._containers[chat_id]
    
    def _execute_in_container(self, chat_id: str, sandbox_id: Optional[str], 
                              command: str, operation_name: str) -> tuple:
        """Execute a command in the container."""
        container = self.get_or_create_sandbox(chat_id, sandbox_id)
        
        try:
            exit_code, output = container.exec_run(
                cmd=["bash", "-c", command],
                workdir="/home/user",
                demux=True,
            )
            
            stdout = output[0].decode('utf-8') if output[0] else ""
            stderr = output[1].decode('utf-8') if output[1] else ""
            
            return exit_code, stdout, stderr, container.id
            
        except Exception as e:
            # Container might have died, try to recreate
            if chat_id in self._containers:
                del self._containers[chat_id]
            
            container = self.get_or_create_sandbox(chat_id, None)
            
            exit_code, output = container.exec_run(
                cmd=["bash", "-c", command],
                workdir="/home/user",
                demux=True,
            )
            
            stdout = output[0].decode('utf-8') if output[0] else ""
            stderr = output[1].decode('utf-8') if output[1] else ""
            
            return exit_code, stdout, stderr, container.id

    # =========================================================================
    # NEW: upload_to_sandbox - Copy uploaded files into the workspace
    # =========================================================================
    def upload_to_sandbox(self, chat_id: str, sandbox_id: Optional[str],
                          source_path: str, filename: str) -> Dict[str, Any]:
        """
        Copy an uploaded file into the sandbox workspace at /home/user/uploads/.
        
        Args:
            chat_id: Chat ID
            sandbox_id: Container ID
            source_path: Path to the source file on the host
            filename: Original filename
        
        Returns:
            Dict with sandbox_path and success status
        """
        try:
            workspace = self._get_workspace(chat_id)
            uploads_dir = workspace / "uploads"
            uploads_dir.mkdir(parents=True, exist_ok=True)
            
            dest_path = uploads_dir / filename
            
            # Copy file to workspace
            shutil.copy2(source_path, str(dest_path))
            
            sandbox_path = f"/home/user/uploads/{filename}"
            size_bytes = os.path.getsize(str(dest_path))
            
            # Ensure container exists
            container = self.get_or_create_sandbox(chat_id, sandbox_id)
            
            print(f"[DOCKER] Uploaded file to sandbox: {sandbox_path} ({size_bytes:,} bytes)")
            
            return {
                "success": True,
                "sandbox_path": sandbox_path,
                "filename": filename,
                "size_bytes": size_bytes,
                "sandbox_id": container.id
            }
            
        except Exception as e:
            error_msg = f"Failed to upload file to sandbox: {str(e)}"
            print(f"[DOCKER] {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "sandbox_id": sandbox_id
            }

    # =========================================================================
    # NEW: execute_bash - Run shell commands in sandbox
    # =========================================================================
    def execute_bash(self, chat_id: str, sandbox_id: Optional[str],
                     command: str, message_id: Optional[str] = None,
                     iteration: int = 0) -> Dict[str, Any]:
        """
        Execute a bash command in the sandbox.
        
        Use for: pip install, ls, cat, head, grep, file operations, etc.
        
        Args:
            chat_id: Chat ID
            sandbox_id: Container ID
            command: Shell command to execute
            message_id: Message ID for logging
            iteration: Iteration number
        
        Returns:
            Dict with execution result
        """
        # Log start
        event_id = None
        if self.db:
            event_id = self.db.log_tool_call(
                chat_id=chat_id,
                tool_name='execute_bash',
                tool_input={'command': command},
                message_id=message_id,
                iteration=iteration
            )
        
        start_time = time.time()
        
        try:
            # Execute in container
            exit_code, stdout, stderr, container_id = self._execute_in_container(
                chat_id, sandbox_id, command, "execute_bash"
            )
            
            execution_time = (time.time() - start_time) * 1000
            
            # Combine output
            output = stdout
            if stderr:
                # For pip install, stderr often contains progress info, not errors
                if exit_code == 0:
                    output = f"{stdout}\n{stderr}" if stdout else stderr
                else:
                    output = stdout  # Keep stdout separate for error case
            
            if exit_code != 0:
                error_msg = stderr or stdout or "Command failed with no output"
                
                result_dict = {
                    "success": False,
                    "output": stdout,
                    "error": error_msg,
                    "exit_code": exit_code,
                    "sandbox_id": container_id
                }
                
                if self.db and event_id:
                    self.db.update_tool_call(
                        event_id=event_id,
                        status='error',
                        error_msg=error_msg,
                        sandbox_id=container_id,
                        execution_time_ms=execution_time
                    )
                
                print(f"[DOCKER] Bash error (exit {exit_code}): {error_msg[:100]}")
                return result_dict
            
            else:
                result_dict = {
                    "success": True,
                    "output": output if output else "Command executed successfully (no output).",
                    "exit_code": 0,
                    "sandbox_id": container_id
                }
                
                if self.db and event_id:
                    self.db.update_tool_call(
                        event_id=event_id,
                        status='success',
                        tool_output={'output_length': len(output or '')},
                        sandbox_id=container_id,
                        execution_time_ms=execution_time
                    )
                
                print(f"[DOCKER] Bash executed successfully in {execution_time:.1f}ms")
                return result_dict
        
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            error_msg = f"Bash execution error: {str(e)}"
            
            result_dict = {
                "success": False,
                "output": "",
                "error": error_msg,
                "exit_code": -1,
                "sandbox_id": sandbox_id
            }
            
            if self.db and event_id:
                self.db.update_tool_call(
                    event_id=event_id,
                    status='error',
                    error_msg=error_msg,
                    execution_time_ms=execution_time
                )
            
            print(f"[DOCKER] Bash system error: {error_msg}")
            return result_dict

    # =========================================================================
    # Existing tools (unchanged in behavior, minor cleanup)
    # =========================================================================

    def create_file(self, chat_id: str, sandbox_id: Optional[str], path: str, content: str,
                   message_id: Optional[str] = None, iteration: int = 0) -> Dict[str, Any]:
        """Create or overwrite a file in the container workspace.
        
        For .py files, automatically runs a syntax check and returns the result.
        Always returns a preview of what was actually written to disk.
        """
        event_id = None
        if self.db:
            event_id = self.db.log_tool_call(
                chat_id=chat_id,
                tool_name='create_file',
                tool_input={'path': path, 'content': content},
                message_id=message_id,
                iteration=iteration
            )
        
        start_time = time.time()
        
        try:
            # Normalize path
            if not path.startswith('/home/user'):
                if path.startswith('/'):
                    path = '/home/user' + path
                else:
                    path = '/home/user/' + path
            
            workspace = self._get_workspace(chat_id)
            relative_path = path.replace('/home/user/', '').replace('/home/user', '')
            local_path = workspace / relative_path
            
            # Create parent directories
            local_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write file
            local_path.write_text(content, encoding='utf-8')
            
            execution_time = (time.time() - start_time) * 1000
            
            container = self.get_or_create_sandbox(chat_id, sandbox_id)
            
            # --- Read back what was actually written (first 20 lines) ---
            written_lines = content.split('\n')
            preview_lines = written_lines[:20]
            preview = '\n'.join(f"  {i+1:3d}| {line}" for i, line in enumerate(preview_lines))
            if len(written_lines) > 20:
                preview += f"\n  ... ({len(written_lines) - 20} more lines)"
            
            result = {
                "success": True,
                "message": f"Created file: {path} ({len(written_lines)} lines, {len(content)} chars)",
                "sandbox_id": container.id,
                "path": path,
                "preview": preview,
            }
            
            # --- Auto syntax check for .py files ---
            if path.endswith('.py'):
                try:
                    import ast
                    ast.parse(content)
                    result["syntax_check"] = "✅ Python syntax OK"
                except SyntaxError as e:
                    result["syntax_check"] = f"❌ SYNTAX ERROR at line {e.lineno}: {e.msg}"
                    result["syntax_error_line"] = e.lineno
                    # Show the problem area
                    if e.lineno and e.lineno <= len(written_lines):
                        start = max(0, e.lineno - 3)
                        end = min(len(written_lines), e.lineno + 2)
                        error_context = '\n'.join(
                            f"  {'>>>' if i+1 == e.lineno else '   '} {i+1:3d}| {written_lines[i]}" 
                            for i in range(start, end)
                        )
                        result["syntax_error_context"] = error_context
            
            if self.db and event_id:
                self.db.update_tool_call(
                    event_id=event_id,
                    status='success',
                    tool_output=result,
                    sandbox_id=container.id,
                    execution_time_ms=execution_time
                )
            
            print(f"[DOCKER] Created file: {path} in {execution_time:.1f}ms")
            if 'syntax_check' in result and '❌' in result['syntax_check']:
                print(f"[DOCKER] ⚠️ Syntax error in {path}: {result['syntax_check']}")
            
            return result
        
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            error_msg = f"Failed to create file: {str(e)}"
            
            result = {
                "success": False,
                "error": error_msg,
                "sandbox_id": sandbox_id
            }
            
            if self.db and event_id:
                self.db.update_tool_call(
                    event_id=event_id,
                    status='error',
                    error_msg=error_msg,
                    execution_time_ms=execution_time
                )
            
            print(f"[DOCKER] Error creating file: {error_msg}")
            return result
    
    def read_file(self, chat_id: str, sandbox_id: Optional[str], path: str,
                 message_id: Optional[str] = None, iteration: int = 0) -> Dict[str, Any]:
        """Read a file from the container workspace."""
        event_id = None
        if self.db:
            event_id = self.db.log_tool_call(
                chat_id=chat_id,
                tool_name='read_file',
                tool_input={'path': path},
                message_id=message_id,
                iteration=iteration
            )
        
        start_time = time.time()
        
        try:
            if not path.startswith('/home/user'):
                if path.startswith('/'):
                    path = '/home/user' + path
                else:
                    path = '/home/user/' + path
            
            workspace = self._get_workspace(chat_id)
            relative_path = path.replace('/home/user/', '').replace('/home/user', '')
            local_path = workspace / relative_path
            
            if not local_path.exists():
                raise FileNotFoundError(f"File not found: {path}")
            
            content = local_path.read_text(encoding='utf-8')
            
            execution_time = (time.time() - start_time) * 1000
            
            container = self.get_or_create_sandbox(chat_id, sandbox_id)
            
            result = {
                "success": True,
                "content": content,
                "path": path,
                "sandbox_id": container.id
            }
            
            if self.db and event_id:
                self.db.update_tool_call(
                    event_id=event_id,
                    status='success',
                    tool_output={'path': path, 'size': len(content)},
                    sandbox_id=container.id,
                    execution_time_ms=execution_time
                )
            
            print(f"[DOCKER] Read file: {path} ({len(content)} chars) in {execution_time:.1f}ms")
            return result
        
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            error_msg = f"Failed to read file: {str(e)}"
            
            result = {
                "success": False,
                "error": error_msg,
                "sandbox_id": sandbox_id
            }
            
            if self.db and event_id:
                self.db.update_tool_call(
                    event_id=event_id,
                    status='error',
                    error_msg=error_msg,
                    execution_time_ms=execution_time
                )
            
            print(f"[DOCKER] Error reading file: {error_msg}")
            return result
    
    def list_files(self, chat_id: str, sandbox_id: Optional[str], directory: str = "/home/user",
                  message_id: Optional[str] = None, iteration: int = 0) -> Dict[str, Any]:
        """List files in a directory."""
        event_id = None
        if self.db:
            event_id = self.db.log_tool_call(
                chat_id=chat_id,
                tool_name='list_files',
                tool_input={'directory': directory},
                message_id=message_id,
                iteration=iteration
            )
        
        start_time = time.time()
        
        try:
            if not directory.startswith('/home/user'):
                if directory.startswith('/'):
                    directory = '/home/user' + directory
                else:
                    directory = '/home/user/' + directory
            
            workspace = self._get_workspace(chat_id)
            relative_dir = directory.replace('/home/user/', '').replace('/home/user', '')
            local_dir = workspace / relative_dir if relative_dir else workspace
            
            files = []
            if local_dir.exists():
                for file_path in local_dir.rglob('*'):
                    if file_path.is_file():
                        rel_path = file_path.relative_to(workspace)
                        size = file_path.stat().st_size
                        files.append(f"/home/user/{rel_path}  ({size:,} bytes)")
            
            execution_time = (time.time() - start_time) * 1000
            
            container = self.get_or_create_sandbox(chat_id, sandbox_id)
            
            output = {
                "success": True,
                "files": files,
                "directory": directory,
                "sandbox_id": container.id
            }
            
            if self.db and event_id:
                self.db.update_tool_call(
                    event_id=event_id,
                    status='success',
                    tool_output={'file_count': len(files)},
                    sandbox_id=container.id,
                    execution_time_ms=execution_time
                )
            
            print(f"[DOCKER] Listed files: {len(files)} files in {directory} ({execution_time:.1f}ms)")
            return output
        
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            error_msg = f"Failed to list files: {str(e)}"
            
            output = {
                "success": False,
                "error": error_msg,
                "sandbox_id": sandbox_id
            }
            
            if self.db and event_id:
                self.db.update_tool_call(
                    event_id=event_id,
                    status='error',
                    error_msg=error_msg,
                    execution_time_ms=execution_time
                )
            
            print(f"[DOCKER] Error listing files: {error_msg}")
            return output
    
    def execute_python(self, chat_id: str, sandbox_id: Optional[str], 
                      code: Optional[str] = None, file_path: Optional[str] = None,
                      message_id: Optional[str] = None, iteration: int = 0) -> Dict[str, Any]:
        """Execute Python code or run a file."""
        event_id = None
        if self.db:
            event_id = self.db.log_tool_call(
                chat_id=chat_id,
                tool_name='execute_python',
                tool_input={'code': code, 'file_path': file_path},
                message_id=message_id,
                iteration=iteration
            )
        
        start_time = time.time()
        
        try:
            if file_path:
                if not file_path.startswith('/home/user'):
                    if file_path.startswith('/'):
                        file_path = '/home/user' + file_path
                    else:
                        file_path = '/home/user/' + file_path
                
                command = f"cd /home/user && python3 {file_path}"
                print(f"[DOCKER] Executing file: {file_path}")
                
            elif code:
                # Write code to a temp file to avoid shell escaping issues
                try:
                    workspace = self._get_workspace(chat_id)
                    temp_script = workspace / "_tmp_exec.py"
                    temp_script.write_text(code, encoding='utf-8')
                    command = "cd /home/user && python3 /home/user/_tmp_exec.py"
                    print(f"[DOCKER] Executing code via temp file ({len(code)} chars)")
                except Exception as write_err:
                    # Fallback: shell escape approach
                    print(f"[DOCKER] Temp file write failed ({write_err}), using shell escape")
                    escaped_code = code.replace("'", "'\"'\"'")
                    command = f"cd /home/user && python3 -c '{escaped_code}'"
                
            else:
                raise ValueError("Either code or file_path must be provided")
            
            exit_code, stdout, stderr, container_id = self._execute_in_container(
                chat_id, sandbox_id, command, "execute_python"
            )
            
            execution_time = (time.time() - start_time) * 1000
            
            # Cleanup temp file (safe - don't crash if cleanup fails)
            if code and not file_path:
                try:
                    workspace = self._get_workspace(chat_id)
                    temp_script = workspace / "_tmp_exec.py"
                    if temp_script.exists():
                        temp_script.unlink()
                except Exception:
                    pass  # Cleanup failure is non-critical
            
            if exit_code != 0:
                error_msg = stderr or stdout or "Unknown error"
                
                result_dict = {
                    "success": False,
                    "output": stdout,
                    "error": error_msg,
                    "error_type": self._classify_error(error_msg),
                    "sandbox_id": container_id
                }
                
                if file_path:
                    result_dict["file_path"] = file_path
                
                if self.db and event_id:
                    self.db.update_tool_call(
                        event_id=event_id,
                        status='error',
                        error_msg=error_msg,
                        sandbox_id=container_id,
                        execution_time_ms=execution_time
                    )
                
                print(f"[DOCKER] Code execution error: {error_msg[:100]}")
                return result_dict
            
            else:
                output = stdout
                if stderr:
                    output = f"{stdout}\n{stderr}" if stdout else stderr
                
                result_dict = {
                    "success": True,
                    "output": output if output else "Code executed successfully with no printed output.",
                    "error": None,
                    "sandbox_id": container_id
                }
                
                if file_path:
                    result_dict["file_path"] = file_path
                
                if self.db and event_id:
                    self.db.update_tool_call(
                        event_id=event_id,
                        status='success',
                        tool_output={'output_length': len(output or '')},
                        sandbox_id=container_id,
                        execution_time_ms=execution_time
                    )
                
                print(f"[DOCKER] Code executed successfully in {execution_time:.1f}ms")
                return result_dict
        
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            error_msg = f"Execution error: {str(e)}"
            
            result_dict = {
                "success": False,
                "output": "",
                "error": error_msg,
                "error_type": "system",
                "sandbox_id": sandbox_id
            }
            
            if self.db and event_id:
                self.db.update_tool_call(
                    event_id=event_id,
                    status='error',
                    error_msg=error_msg,
                    execution_time_ms=execution_time
                )
            
            print(f"[DOCKER] System error: {error_msg}")
            return result_dict
    
    def _classify_error(self, error_msg: str) -> str:
        """Classify error type to help determine if it's fixable."""
        error_lower = error_msg.lower()
        
        environmental_keywords = [
            'network', 'connection', 'unreachable', 'dns',
            'no route to host', 'timeout', 'timed out',
            'ssl', 'certificate', 'url', 'socket',
            'permission denied', 'access denied',
            'api_key', 'api key', 'authentication', 'unauthorized'
        ]
        
        for keyword in environmental_keywords:
            if keyword in error_lower:
                return "environmental"
        
        return "code"
    
    def _detect_file_type(self, filename: str) -> str:
        """Detect file type from extension."""
        ext = filename.split('.')[-1].lower() if '.' in filename else ''
        
        type_map = {
            'py': 'python',
            'js': 'javascript',
            'ts': 'typescript',
            'jsx': 'javascript',
            'tsx': 'typescript',
            'html': 'html',
            'css': 'css',
            'json': 'json',
            'md': 'markdown',
            'txt': 'text',
            'csv': 'csv',
            'xml': 'xml',
            'yaml': 'yaml',
            'yml': 'yaml',
            'sh': 'bash',
            'sql': 'sql',
            'env': 'text',
            'xlsx': 'excel',
            'xls': 'excel',
        }
        
        return type_map.get(ext, 'text')
    
    # Binary file extensions that should be read as bytes and base64-encoded
    BINARY_EXTENSIONS = {
        'xlsx', 'xls', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp',
        'zip', 'tar', 'gz', 'bz2', '7z', 'rar',
        'parquet', 'pickle', 'pkl',
        'sqlite', 'db',
        'doc', 'docx', 'pptx', 'ppt',
        'mp3', 'wav', 'mp4', 'avi',
        'woff', 'woff2', 'ttf', 'otf',
    }

    def _is_binary_file(self, filename: str) -> bool:
        """Check if a file should be treated as binary based on extension."""
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        return ext in self.BINARY_EXTENSIONS

    def save_files(self, chat_id: str, sandbox_id: Optional[str], files: list,
                   message_id: Optional[str] = None, iteration: int = 0) -> Dict[str, Any]:
        """Save one or more files from the workspace to persistent database storage.
        
        Handles both text and binary files. Binary files are base64-encoded for storage.
        
        Args:
            files: List of dicts with 'filepath' and optional 'description'
        """
        import base64

        MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB per file
        
        event_id = None
        if self.db:
            event_id = self.db.log_tool_call(
                chat_id=chat_id,
                tool_name='save_files',
                tool_input={'files': files},
                message_id=message_id,
                iteration=iteration
            )
        
        start_time = time.time()
        results = []
        
        for file_entry in files:
            filepath = file_entry.get('filepath', '')
            description = file_entry.get('description', '')
            
            try:
                if not filepath.startswith('/home/user'):
                    if filepath.startswith('/'):
                        filepath = '/home/user' + filepath
                    else:
                        filepath = '/home/user/' + filepath
                
                workspace = self._get_workspace(chat_id)
                relative_path = filepath.replace('/home/user/', '').replace('/home/user', '')
                local_path = workspace / relative_path
                
                if not local_path.exists():
                    results.append({
                        "filepath": filepath,
                        "success": False,
                        "error": f"File not found: {filepath}. Create it first with create_file."
                    })
                    continue
                
                filename = os.path.basename(filepath)
                is_binary = self._is_binary_file(filename)
                
                if is_binary:
                    raw_bytes = local_path.read_bytes()
                    size_bytes = len(raw_bytes)
                    content = base64.b64encode(raw_bytes).decode('ascii')
                else:
                    content = local_path.read_text(encoding='utf-8')
                    size_bytes = len(content.encode('utf-8'))
                
                if size_bytes > MAX_FILE_SIZE:
                    results.append({
                        "filepath": filepath,
                        "success": False,
                        "error": f"File too large ({size_bytes} bytes). Maximum: {MAX_FILE_SIZE} bytes"
                    })
                    continue
                
                directory = os.path.dirname(relative_path)
                if directory and not directory.endswith('/'):
                    directory += '/'
                if not directory:
                    directory = None
                
                file_type = self._detect_file_type(filename)
                
                container = self.get_or_create_sandbox(chat_id, sandbox_id)
                
                if self.db:
                    existing = self.db.get_sandbox_file(chat_id, filepath)
                    
                    if existing:
                        self.db.update_sandbox_file(
                            existing.id,
                            content=content,
                            size_bytes=size_bytes,
                            is_binary=is_binary
                        )
                        action = "updated"
                    else:
                        self.db.add_sandbox_file(
                            chat_id=chat_id,
                            filepath=filepath,
                            filename=filename,
                            directory=directory,
                            content=content,
                            description=description,
                            file_type=file_type,
                            size_bytes=size_bytes,
                            is_binary=is_binary
                        )
                        action = "created"
                else:
                    action = "created"
                
                results.append({
                    "filepath": filepath,
                    "filename": filename,
                    "success": True,
                    "action": action,
                    "size": size_bytes,
                    "file_type": file_type,
                    "is_binary": is_binary
                })
                
                print(f"[DOCKER] File saved: {filepath} ({action}, {'binary' if is_binary else 'text'}, {size_bytes} bytes)")
            
            except Exception as e:
                results.append({
                    "filepath": filepath,
                    "success": False,
                    "error": str(e)
                })
                print(f"[DOCKER] Error saving file {filepath}: {e}")
        
        execution_time = (time.time() - start_time) * 1000
        
        succeeded = [r for r in results if r.get('success')]
        failed = [r for r in results if not r.get('success')]
        
        overall_result = {
            "success": len(failed) == 0,
            "saved": len(succeeded),
            "failed": len(failed),
            "total": len(results),
            "files": results,
            "sandbox_id": sandbox_id
        }
        
        if self.db and event_id:
            self.db.update_tool_call(
                event_id=event_id,
                status='success' if len(failed) == 0 else ('error' if len(succeeded) == 0 else 'partial'),
                tool_output=overall_result,
                sandbox_id=sandbox_id,
                execution_time_ms=execution_time,
                error_msg=f"{len(failed)} file(s) failed" if failed else None
            )
        
        print(f"[DOCKER] save_files complete: {len(succeeded)} saved, {len(failed)} failed in {execution_time:.1f}ms")
        return overall_result
    
    def get_sandbox_id(self, chat_id: str) -> Optional[str]:
        """Get container ID for a chat if it exists."""
        if chat_id in self._containers:
            return self._containers[chat_id].id
        return None
    
    def cleanup_all(self):
        """Clean up all containers (call on app shutdown)."""
        print(f"[DOCKER] Cleaning up {len(self._containers)} containers...")
        for chat_id in list(self._containers.keys()):
            self.close_sandbox(chat_id)
        print("[DOCKER] Cleanup complete")
    
    def cleanup_orphaned_containers(self):
        """Remove any orphaned containers from previous runs."""
        try:
            containers = self.docker_client.containers.list(
                all=True,
                filters={"label": f"{self.CONTAINER_LABEL}=true"}
            )
            for container in containers:
                try:
                    container.stop(timeout=2)
                    container.remove(force=True)
                    print(f"[DOCKER] Removed orphaned container: {container.id[:12]}")
                except Exception as e:
                    print(f"[DOCKER] Error removing orphaned container: {e}")
        except Exception as e:
            print(f"[DOCKER] Error listing orphaned containers: {e}")
