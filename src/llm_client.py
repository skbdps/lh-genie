"""LLM Client for Databricks Model Serving Endpoints

This client communicates with a Databricks-hosted model serving endpoint
that wraps Gemini (or any OpenAI-compatible model) and handles:
- Message conversion to OpenAI format
- Multi-turn tool calling with proper message structure
- Error handling and retry logic

CODE-FIRST improvements:
1. System prompt instructs model to use code for data processing
2. New execute_bash tool for shell commands / pip install
3. New upload_to_sandbox tool to make uploaded files available
4. Planning-first approach for complex tasks
5. File-aware: knows when files are in sandbox vs in message
"""

import os
import time
import json
from typing import Dict, Any, Optional, List, Callable
from collections import Counter
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class LLMClient:
    """LLM client for Databricks model serving endpoints."""
    
    # Error patterns that indicate environmental issues (shouldn't retry)
    UNSOLVABLE_ERROR_PATTERNS = [
        "api_key", "api key", "apikey",
        "authentication", "unauthorized", "403", "401",
        "quota exceeded", "rate limit", "too many requests", "429",
        "permission denied", "access denied",
        "connection refused", "connection reset",
        "network unreachable", "network error",
        "dns", "name resolution",
        "ssl", "certificate",
    ]
    
    # Transient errors that may be worth retrying
    TRANSIENT_ERROR_PATTERNS = [
        "timeout", "timed out",
        "service unavailable", "503",
        "internal server error", "500",
        "bad gateway", "502",
    ]
    
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3-flash-preview",
        max_tokens: int = 65536,
        base_url: Optional[str] = None,
        endpoint_name: Optional[str] = None,
        db=None
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.db = db
        self.base_url = base_url
        self.api_key = api_key
        self.endpoint_name = endpoint_name
        
        # CODE-FIRST system prompt
        self.system_prompt = self._build_system_prompt()

    # -------------------------------------------------------------------------
    # System Prompt (Code-First)
    # -------------------------------------------------------------------------
    def _build_system_prompt(self) -> str:
        return """You are a code-first AI assistant with a persistent Python sandbox (Docker container). You have tools to create files, execute Python code, run bash commands, and manage a workspace. Don't chain running code scripts with &&, run each code script separately and always verify test output explicitly.
        You have to plan your task according to this only. Do not try to run multiple scripts at once unless logically necessary.

Important rules for library installation:
1. For pip install calls, you will have to set pypi as trusted host in the command.
2. Research the web for latest functionalities and plan your installations accordingly.
3. Do not try to install different versions of the same library if not necessary.

## CORE PRINCIPLE: CODE-FIRST

For ANY task involving data, files, computation, or verification — **always use code**. Never try to process data by reading it inline in the conversation. Instead:

1. **Check if files are in the sandbox** using `list_files`
2. **Write code** to read, process, and analyze data
3. **Execute and verify** the results
4. **Iterate** if there are errors

## PLANNING

For complex tasks (multi-step, multi-file, or data processing), ALWAYS plan first:

1. **State your plan** briefly in text before any tool calls
2. **Break into steps** — create files, install deps, execute, verify
3. **Verify results** — always print output, run tests, check data

## FILE HANDLING RULES

- **Uploaded files** are automatically copied to `/home/user/uploads/` in the sandbox
- **NEVER try to "see" or reason about large file contents from the message** — always use code to read them
- For Excel/CSV/JSON data: use pandas, openpyxl, or appropriate libraries in code
- For PDFs: use PyMuPDF or pdfplumber in code
- For images: use PIL/Pillow in code
- Install any needed packages with `execute_bash` first: `pip install pandas openpyxl`


## CODE QUALITY

- Always add `print()` statements so you can see results
- Use try/except for error handling in complex scripts
- For data tasks, always print shape, dtypes, head(), sample data
- Write modular code — separate files for utilities, main logic, tests

## TESTING AND VERIFICATION

When writing code or generating data:
1. **Run each script separately** — do NOT chain scripts with `&&` in bash. Run each one individually so you can see its output and catch errors early.
2. **Check test output** — after running tests, verify the output explicitly shows all tests passed before declaring success.
3. **Create test cases if needed** — write a test script that validates output

## IMPORTANT BEHAVIORS

- Be concise for simple questions — don't use tools unless needed
- For data tasks (Excel, CSV, database queries), ALWAYS use code
- If a file is mentioned as uploaded, it's at `/home/user/uploads/<filename>`
- Don't apologize or explain at length — just do the work
- If you need a package, install it first, then use it
- When generating SQL INSERT statements from data: always use code to read the data file and generate the SQL programmatically
- After completing a task, always use `save_files` to persist all final output files in one call so the user can download them.
## COMMON PYTHON GOTCHAS

- In Python, `bool` is a subclass of `int`. Always check `isinstance(val, bool)` BEFORE `isinstance(val, int)`, otherwise `True` becomes `1` and `False` becomes `0`.
- Avoid nested quotes inside f-strings. Use helper variables or triple-quoted strings instead.

## ANTI-PATTERNS (DO NOT DO THESE)

- ❌ Trying to read Excel data from the message text
- ❌ Manually typing out data you see in a file
- ❌ Writing SQL INSERTs by hand from data you "see"
- ❌ Processing >10 rows of data without code
- ❌ Skipping verification after generating output
- ❌ Using execute_python for pip install (use execute_bash instead)

## TRINO LAKEHOUSE ACCESS

You have access to the company's Starburst Trino lakehouse via these tools:
- `health_check` — verify Trino is reachable
- `list_schemas` — discover available schemas
- `list_tables(schema)` — list tables in a schema
- `describe_table(schema, table)` — get column names, types, row count
- `run_query(sql, limit)` — execute a SELECT query (read-only, max 500 rows)

### DATA EXPLORATION WORKFLOW

When the user asks a data question:
1. Call `list_schemas` to discover what's available
2. Call `list_tables` on the relevant schema
3. Call `describe_table` to understand columns BEFORE writing SQL
4. Call `run_query` with a precise, qualified SELECT (catalog.schema.table)
5. Query results are automatically saved to /home/user/query_results/ as JSON
6. Use Python in the sandbox to analyze/visualize the results:
   ```python
   import json
   from lib.lakehouse_utils import query_to_df, display_table, display_chart, save_chart

   with open('/home/user/query_results/query_001.json') as f:
       data = json.load(f)
   df = query_to_df(data['rows'], data['columns'])

   display_table(df, title="Results")
   fig = display_chart(df, x="col1", y="col2", kind="bar", title="Chart Title")
   save_chart(fig)
   ```
7. Call `save_files` to persist outputs for download

### LAKEHOUSE RULES
- Only SELECT queries are allowed — no INSERT, UPDATE, DELETE, DDL
- Always qualify tables as: catalog.schema.table
- Always call describe_table before writing a query to get exact column names
- Results are capped at 500 rows; use WHERE clauses to be precise
- Query results are saved as JSON files in the sandbox — load them with code

### HELPER LIBRARY (pre-installed in sandbox)
The module at /home/user/lib/lakehouse_utils.py provides:
- `query_to_df(rows, columns)` — converts JSON rows to a typed pandas DataFrame
- `display_table(df, title, max_rows)` — renders a styled HTML table to /home/user/output/
- `display_chart(df, x, y, kind, title)` — creates a plotly Figure (returns Figure for customization)
- `save_chart(fig, output_path)` — saves chart as interactive HTML + static PNG
- `save_table(df, output_path, fmt)` — exports DataFrame to csv/xlsx/json

Chart types: bar, barh, line, scatter, pie, histogram, heatmap
For simple tables and charts, use the helpers. For complex/custom visualizations, write raw plotly or matplotlib code."""

    # -------------------------------------------------------------------------
    # Tool Definitions
    # -------------------------------------------------------------------------
    def _get_tools(self) -> List[Dict[str, Any]]:
        """Get tool definitions in OpenAI format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "create_file",
                    "description": """Create or overwrite a file in the project workspace.

Use this to:
- Create Python scripts, modules, or data files
- Build multi-file projects with proper structure
- Write configuration files

The workspace is persistent. Files remain available for import and execution.
Base directory: /home/user/ (e.g., /home/user/main.py)
Uploaded files are at: /home/user/uploads/""",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "File path starting with /home/user/ (e.g., /home/user/process_data.py)"
                            },
                            "content": {
                                "type": "string",
                                "description": "Complete file content"
                            }
                        },
                        "required": ["path", "content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "execute_python",
                    "description": """Execute Python code or run a Python file in the sandbox.

Use this to:
- Run data processing scripts
- Test code you've written
- Quick computations with the code parameter
- Run complete files with the file_path parameter

IMPORTANT: Install packages with execute_bash first, not here.
For complex scripts, create a file first then run it.""",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": "Python code to execute directly (for quick tasks)"
                            },
                            "file_path": {
                                "type": "string",
                                "description": "Path to a Python file to execute (for complex scripts)"
                            }
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "execute_bash",
                    "description": """Execute a bash/shell command in the sandbox.

Example syntax recommended to use:
- Install Python packages: pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org pandas openpyxl faker
- Run shell commands: ls, cat, head, wc -l, find, grep
- Check file sizes and types: file, du, stat
- Any system-level operation

ALWAYS use this for pip install instead of execute_python.
Commands run in /home/user/ directory.""",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "Shell command to execute (e.g., 'pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org pandas', 'ls -la uploads/', 'head -20 uploads/data.csv')"
                            }
                        },
                        "required": ["command"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": """Read the contents of a file from the workspace.

Use this to:
- Review code you've written
- Check file contents before editing
- Read small text files

For large data files (CSV, Excel, JSON), prefer using Python code to read
and process them rather than this tool.""",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "File path to read (e.g., /home/user/output.txt)"
                            }
                        },
                        "required": ["path"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": """List all files in a directory of the workspace.

Use this to:
- See what files exist, including uploaded files in /home/user/uploads/
- Check project structure
- Find files before editing""",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "directory": {
                                "type": "string",
                                "description": "Directory to list (default: /home/user)",
                                "default": "/home/user"
                            }
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "save_files",
                    "description": """Save one or more files from the sandbox to persistent storage (database) in a single call.

Use this after completing a task to persist final output files so the user can download them.
Supports both text and binary files (xlsx, pdf, images, etc.).
Always call this once at the end with ALL files to save, rather than calling multiple times.""",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "files": {
                                "type": "array",
                                "description": "List of files to save",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "filepath": {
                                            "type": "string",
                                            "description": "Path to the file in the sandbox"
                                        },
                                        "description": {
                                            "type": "string",
                                            "description": "Brief description of the file"
                                        }
                                    },
                                    "required": ["filepath"]
                                }
                            }
                        },
                        "required": ["files"]
                    }
                }
            },
            # ---- Trino Lakehouse Tools ----
            {
                "type": "function",
                "function": {
                    "name": "health_check",
                    "description": "Check if the Trino lakehouse is reachable. Use this before running queries if you're unsure about connectivity. Returns server status, catalog name, and configuration limits.",
                    "parameters": {
                        "type": "object",
                        "properties": {}
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "list_schemas",
                    "description": "List all available schemas in the Trino lakehouse catalog. Call this first to discover what data is available before exploring tables.",
                    "parameters": {
                        "type": "object",
                        "properties": {}
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "list_tables",
                    "description": "List all tables in a specific schema. Call this after list_schemas to explore what tables exist in a schema.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "schema": {
                                "type": "string",
                                "description": "Schema name to list tables from (e.g. 'di_metadata')"
                            }
                        },
                        "required": ["schema"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "describe_table",
                    "description": "Get column definitions (names, data types, comments) and approximate row count for a table. ALWAYS call this before writing a query — it prevents column name and type errors.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "schema": {
                                "type": "string",
                                "description": "Schema name (e.g. 'di_metadata')"
                            },
                            "table": {
                                "type": "string",
                                "description": "Table name to describe"
                            }
                        },
                        "required": ["schema", "table"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "run_query",
                    "description": """Execute a read-only SELECT query against the Trino lakehouse and return results.

Guardrails are applied automatically:
- Only SELECT allowed (no writes, DDL, or system catalog access)
- Results capped at 500 rows (use the limit parameter or WHERE clauses)
- Schema allowlist enforced
- Per-user RBAC via Starburst impersonation

Before calling this:
1. Use describe_table to confirm exact column names and types.
2. Qualify all table names as catalog.schema.table.
3. Prefer targeted WHERE clauses over full-table scans.

Results are automatically saved as JSON in /home/user/query_results/ for code analysis.""",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "sql": {
                                "type": "string",
                                "description": "A SELECT SQL query in Trino SQL dialect. Must reference tables as catalog.schema.table."
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum rows to return (1-500, default 100). Hard-capped at server's MAX_ROWS setting.",
                                "default": 100
                            }
                        },
                        "required": ["sql"]
                    }
                }
            }
        ]

    # -------------------------------------------------------------------------
    # Error Classification
    # -------------------------------------------------------------------------
    def _is_unsolvable_error(self, error_msg: str) -> bool:
        """Check if an error is unsolvable (environmental issue)."""
        error_lower = error_msg.lower()
        return any(pattern in error_lower for pattern in self.UNSOLVABLE_ERROR_PATTERNS)
    
    def _is_transient_error(self, error_msg: str) -> bool:
        """Check if an error might be transient and worth retrying."""
        error_lower = error_msg.lower()
        return any(pattern in error_lower for pattern in self.TRANSIENT_ERROR_PATTERNS)

    def _get_error_signature(self, error_msg: str) -> str:
        """Get a normalized signature for an error message for deduplication."""
        import re
        signature = error_msg.lower()
        signature = re.sub(r'line \d+', 'line N', signature)
        signature = re.sub(r'0x[0-9a-f]+', '0xADDR', signature)
        signature = re.sub(r'/home/user/[^\s]+', '/home/user/FILE', signature)
        signature = re.sub(r'\d+\.\d+\.\d+', 'X.X.X', signature)
        return signature[:200]

    def _build_error_context(
        self,
        error_msg: str,
        error_count: int,
        is_unsolvable: bool,
        iteration: int,
        max_iterations: int
    ) -> str:
        """Build context message based on error severity."""
        if is_unsolvable:
            return f"""⚠️ UNSOLVABLE ERROR - DO NOT RETRY ⚠️

This error indicates an ENVIRONMENTAL issue that cannot be fixed by modifying code:
{error_msg}

ACTION REQUIRED: Stop attempting to fix this. Explain to the user what external action they need to take (e.g., install package, set API key, check network)."""

        if error_count >= 3:
            return f"""🚨 REPEATED ERROR (seen {error_count} times) - STOP RETRYING

Error: {error_msg}

You have tried to fix this error multiple times without success. 
STOP attempting the same approach. Instead:
1. Explain what you've tried
2. Describe what might be causing the persistent failure
3. Ask the user for guidance or suggest alternative approaches"""

        if iteration >= max_iterations - 2:
            return f"""⚠️ APPROACHING ITERATION LIMIT ({iteration}/{max_iterations})

Error: {error_msg}

You are running low on iterations. If you cannot fix this quickly, summarize the issue and ask for user guidance."""

        if error_count == 2:
            return f"""⚠️ SECOND OCCURRENCE of this error type

Error: {error_msg}

This is the second time you're seeing this error. If your next fix attempt fails, you should STOP and explain the issue to the user rather than continuing to retry."""

        return f"Error: {error_msg}"

    # -------------------------------------------------------------------------
    # Message Conversion
    # -------------------------------------------------------------------------
    def _convert_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert internal rich messages to OpenAI-style messages.
        
        Handles:
        - Text content (plain strings or content blocks)
        - Tool use blocks in assistant messages
        - Tool result blocks
        - Document/image placeholders
        - Sandbox file references (code-first approach)
        """
        openai_messages = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # --- USER MESSAGES ---
            if role == "user":
                if isinstance(content, list):
                    text_parts = []

                    for block in content:
                        if not isinstance(block, dict):
                            continue

                        btype = block.get("type")

                        if btype == "text":
                            text_parts.append(block.get("text", ""))

                        elif btype == "sandbox_file_ref":
                            # CODE-FIRST: File is in sandbox, tell model where
                            filename = block.get("filename", "unknown")
                            sandbox_path = block.get("sandbox_path", "")
                            file_type = block.get("file_type", "")
                            size = block.get("size_bytes", 0)
                            size_str = f"{size:,}" if size else "unknown"
                            
                            hint = (
                                f"[📎 File uploaded to sandbox: {filename} "
                                f"(type: {file_type}, size: {size_str} bytes)\n"
                                f"Available at: {sandbox_path}\n"
                                f"Use code to read and process this file. "
                                f"Do NOT try to read it from the message.]"
                            )
                            text_parts.append(hint)

                        elif btype == "document":
                            title = block.get("filename") or block.get("title") or ""
                            hint = f"[Attached document: {title} — text provided earlier or omitted]"
                            text_parts.append(hint)

                        elif btype in ("image", "image_url"):
                            filename = block.get("filename") or block.get("source", {}).get("filename", "")
                            caption = block.get("caption") or block.get("alt", "") or ""
                            hint = f"[Image attached: {filename} {('- ' + caption) if caption else ''}]"
                            text_parts.append(hint)

                        elif btype == "tool_result":
                            tool_content = block.get("content", "")
                            if isinstance(tool_content, list):
                                tool_content = "\n".join(
                                    item.get("text", str(item)) if isinstance(item, dict) else str(item)
                                    for item in tool_content
                                )
                            func_name = block.get("name", "")
                            openai_messages.append({
                                "role": "tool",
                                "tool_call_id": block.get("tool_use_id"),
                                "name": func_name,
                                "content": str(tool_content)
                            })

                        else:
                            text_parts.append(str(block.get("text", block.get("content", ""))))

                    if text_parts:
                        openai_messages.append({
                            "role": "user",
                            "content": "\n".join([p for p in text_parts if p]).strip()
                        })
                else:
                    openai_messages.append({
                        "role": "user",
                        "content": str(content)
                    })

            # --- ASSISTANT MESSAGES ---
            elif role == "assistant":
                if isinstance(content, list):
                    text_parts = []
                    tool_calls = []

                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")

                        if btype == "text":
                            text_parts.append(block.get("text", ""))

                        elif btype == "tool_use":
                            tool_calls.append({
                                "id": block.get("id"),
                                "type": "function",
                                "function": {
                                    "name": block.get("name"),
                                    "arguments": json.dumps(block.get("input", {}))
                                }
                            })

                    assistant_msg = {"role": "assistant"}
                    text_content = "\n".join([t for t in text_parts if t]).strip()
                    assistant_msg["content"] = text_content if text_content else None
                    if tool_calls:
                        assistant_msg["tool_calls"] = tool_calls

                    openai_messages.append(assistant_msg)
                else:
                    openai_messages.append({
                        "role": "assistant",
                        "content": str(content) if content else None
                    })

            # --- TOOL MESSAGES ---
            elif role == "tool":
                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id"),
                    "name": msg.get("name", ""),
                    "content": str(content)
                })

            # --- SYSTEM MESSAGES ---
            elif role == "system":
                openai_messages.append({
                    "role": "system",
                    "content": str(content)
                })

            else:
                openai_messages.append({
                    "role": role,
                    "content": str(content)
                })

        return openai_messages

    # -------------------------------------------------------------------------
    # Tool Result Formatting
    # -------------------------------------------------------------------------
    def _format_tool_result(self, result: Dict[str, Any]) -> str:
        """Format tool result for the model."""
        if result.get("success"):
            output_parts = []
            
            if "output" in result:
                output_parts.append(f"Output:\n{result['output']}")
            if "content" in result:
                output_parts.append(f"Content:\n{result['content']}")
            if "message" in result:
                output_parts.append(result["message"])
            if "files" in result:
                output_parts.append(f"Files: {result['files']}")
            
            return self._truncate_result("\n".join(output_parts) if output_parts else "Success")
        else:
            # Include BOTH stdout and stderr so model gets full context
            parts = []
            if result.get("output"):
                parts.append(f"Stdout:\n{result['output']}")
            if result.get("error"):
                parts.append(f"Error:\n{result['error']}")
            return self._truncate_result("\n".join(parts) if parts else "Unknown error")
    
    def _truncate_result(self, text: str, max_chars: int = 8000) -> str:
        """Truncate tool result to prevent context bloat over many iterations."""
        if len(text) <= max_chars:
            return text
        # Keep beginning and end (most useful for tracebacks)
        half = max_chars // 2
        return (
            text[:half] 
            + f"\n\n... [TRUNCATED — {len(text):,} chars total, showing first and last {half:,}] ...\n\n" 
            + text[-half:]
        )

    # -------------------------------------------------------------------------
    # Context Trimming
    # -------------------------------------------------------------------------
    def _trim_old_tool_results(self, messages: list, keep_recent: int = 8):
        """Compress older tool exchange rounds to prevent context bloat.
        
        Strategy:
        - We NEVER touch assistant tool_call arguments (the model needs to know
          what it wrote, and truncating create_file content makes it think files
          are incomplete, triggering rewrite loops).
        - We collapse old (assistant + tool_result) rounds into a compact
          summary message that replaces both the assistant and tool messages.
        
        OpenAI format requires: every assistant tool_call has a matching 
        tool-role response. So we can't remove one without the other.
        We replace old rounds with: assistant(text summary, no tool_calls).
        """
        # Find "rounds": an assistant message with tool_calls followed by
        # its matching tool-role messages
        rounds = []  # list of (start_idx, end_idx, summary_info)
        i = 0
        while i < len(messages):
            msg = messages[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                round_start = i
                tool_call_ids = {tc["id"] for tc in msg.get("tool_calls", [])}
                
                # Collect the matching tool results
                j = i + 1
                while j < len(messages) and messages[j].get("role") == "tool":
                    j += 1
                round_end = j  # exclusive
                
                # Build summary of this round
                tool_calls = msg.get("tool_calls", [])
                summaries = []
                for tc in tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "?")
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    
                    if name == "create_file":
                        path = args.get("path", "?")
                        content_len = len(args.get("content", ""))
                        summaries.append(f"create_file({path}, {content_len} chars)")
                    elif name == "execute_python":
                        target = args.get("file_path") or "inline code"
                        summaries.append(f"execute_python({target})")
                    elif name == "execute_bash":
                        cmd = (args.get("command") or "")[:60]
                        summaries.append(f"execute_bash({cmd})")
                    elif name == "run_query":
                        sql_preview = (args.get("sql") or "")[:60]
                        summaries.append(f"run_query({sql_preview}...)")
                    elif name in ("list_schemas", "list_tables", "describe_table", "health_check"):
                        schema = args.get("schema", "")
                        table = args.get("table", "")
                        label = f"({schema}.{table})" if table else f"({schema})" if schema else ""
                        summaries.append(f"{name}{label}")
                    else:
                        summaries.append(name)
                
                # Get result status from tool messages
                tool_msgs = messages[round_start+1:round_end]
                for tm in tool_msgs:
                    content = tm.get("content", "")
                    if "Error" in content or "error" in content:
                        summaries.append("→ ERROR")
                        # Keep first 150 chars of the error
                        error_preview = content.split('\n')[0][:150]
                        summaries.append(f"  {error_preview}")
                    else:
                        result_preview = content.split('\n')[0][:100]
                        summaries.append(f"→ {result_preview}")
                
                rounds.append((round_start, round_end, summaries))
                i = round_end
            else:
                i += 1
        
        if len(rounds) <= keep_recent // 2:
            return  # Not enough rounds to bother trimming
        
        # Keep the last N/2 rounds detailed, collapse the rest
        rounds_to_collapse = rounds[:-(keep_recent // 2)]
        
        # Process in reverse order so indices stay valid
        for round_start, round_end, summaries in reversed(rounds_to_collapse):
            summary_text = "[Previous tool round] " + " | ".join(summaries)
            
            # Replace the entire round with a single assistant text message
            replacement = {
                "role": "assistant",
                "content": summary_text
            }
            messages[round_start:round_end] = [replacement]

    # -------------------------------------------------------------------------
    # Title Generation
    # -------------------------------------------------------------------------
    def generate_title(self, user_message: str) -> str:
        """Generate a short chat title from the first user message via LLM."""
        if not self.base_url or not self.endpoint_name:
            return user_message[:40]

        url = f"{self.base_url}/serving-endpoints/{self.endpoint_name}/invocations"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "inputs": [{
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "Generate a short title (max 6 words) for a chat that starts with the following message. Reply with ONLY the title, nothing else."},
                    {"role": "user", "content": user_message[:500]}
                ],
                "temperature": 0.0,
                "max_tokens": 300
            }]
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, verify=False, timeout=15)
            resp.raise_for_status()
            result = resp.json()
            preds = result.get("predictions", [])
            pred = preds[0] if isinstance(preds, list) and preds else preds
            choices = pred.get("choices", [])
            if choices:
                title = choices[0].get("message", {}).get("content", "").strip().strip('"\'')
                if title:
                    return title[:50]
        except Exception as e:
            print(f"[LLM] Title generation failed: {e}")

        return user_message[:40]

    # -------------------------------------------------------------------------
    # Main Send Message Method
    # -------------------------------------------------------------------------
    def send_message(
        self,
        messages: List[Dict[str, Any]],
        chat_id: Optional[str] = None,
        message_id: Optional[str] = None,
        tool_executor: Optional[Callable] = None,
        max_iterations: int = 10
    ) -> Dict[str, Any]:
        """
        Send message using Databricks serving endpoint.
        
        This method handles the agentic loop:
        1. Send messages to the model
        2. If model returns tool calls, execute them
        3. Send results back to the model
        4. Repeat until model returns final text or max iterations reached
        """
        if not self.base_url or not self.endpoint_name:
            raise ValueError("base_url or endpoint_name not configured for LLMClient")

        url = f"{self.base_url}/serving-endpoints/{self.endpoint_name}/invocations"

        # Convert to OpenAI-style messages and prepend system prompt
        openai_messages = self._convert_messages(messages)
        openai_messages.insert(0, {"role": "system", "content": self.system_prompt})

        payload = {
            "inputs": [{
                "model": self.model,
                "messages": openai_messages,
                "temperature": 0.0,
                "max_tokens": self.max_tokens
            }]
        }

        # Add tools if executor is provided
        if tool_executor:
            payload["inputs"][0]["tools"] = self._get_tools()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        # Track all tool calls for UI display
        all_tool_calls = []
        
        # Track accumulated text (model may say something before/after tool calls)
        accumulated_text = []
        
        # Token tracking
        total_input_tokens = 0
        total_output_tokens = 0
        
        # Error tracking
        consecutive_errors = 0

        # Mutable message buffer for iterations (keeps system at index 0)
        messages_for_iterations = list(openai_messages)

        iteration = 0
        while iteration < max_iterations:
            iteration += 1
            
            try:
                # Trim older tool results to prevent context bloat
                if iteration > 5:
                    self._trim_old_tool_results(messages_for_iterations, keep_recent=8)
                
                payload["inputs"][0]["messages"] = messages_for_iterations

                resp = requests.post(url, headers=headers, json=payload, verify=False, timeout=300)
                resp.raise_for_status()
                result = resp.json()

                # --- Parse response ---
                preds = result.get("predictions")
                if isinstance(preds, list) and preds:
                    prediction = preds[0]
                elif isinstance(preds, dict):
                    prediction = preds
                else:
                    raise Exception("Unexpected predictions format from serving endpoint")

                choices = prediction.get("choices") or []
                choice = choices[0] if choices else None
                
                if choice:
                    message_obj = choice.get("message", {})
                    finish_reason = choice.get("finish_reason")
                else:
                    message_obj = prediction.get("message") or {"content": prediction.get("content", "")}
                    finish_reason = None

                # Extract text content
                content_field = message_obj.get("content")
                if isinstance(content_field, dict):
                    message_text = content_field.get("text") or json.dumps(content_field)
                else:
                    message_text = content_field

                # Track usage
                usage = prediction.get("usage", {}) or {}
                total_input_tokens += usage.get("prompt_tokens", 0)
                total_output_tokens += usage.get("completion_tokens", 0)

                # Extract tool calls
                tool_calls_from_model = message_obj.get("tool_calls") or []

                # Accumulate any text content from this response
                if message_text and str(message_text).strip():
                    accumulated_text.append(str(message_text))

                # --- If no tool calls or no executor, return final response ---
                if (not tool_calls_from_model) or (not tool_executor):
                    final_text = "\n\n".join(accumulated_text) if accumulated_text else ""
                    
                    if tool_calls_from_model and not tool_executor:
                        final_text = final_text or "The model requested tool execution but no tool executor is available."
                    
                    # Safeguard: if we did tool calls but model returned no text,
                    # generate a minimal summary so the user isn't left with a blank response
                    if not final_text.strip() and all_tool_calls:
                        error_calls = [tc for tc in all_tool_calls if not tc['result'].get('success', True)]
                        success_calls = [tc for tc in all_tool_calls if tc['result'].get('success', True)]
                        parts = []
                        if success_calls:
                            parts.append(f"Completed {len(success_calls)} operation(s).")
                        if error_calls:
                            last_error = error_calls[-1]
                            err_msg = last_error['result'].get('error', 'Unknown error')
                            # Show first 2 lines of error
                            err_preview = '\n'.join(err_msg.strip().split('\n')[:2])
                            parts.append(f"Encountered an error:\n```\n{err_preview}\n```")
                        final_text = " ".join(parts) if parts else "Processing complete."
                    
                    return {
                        "content": [{"type": "text", "text": final_text}],
                        "usage": {
                            "input_tokens": total_input_tokens,
                            "output_tokens": total_output_tokens
                        },
                        "tool_calls": all_tool_calls,
                        "stop_reason": finish_reason or "stop",
                        "max_iterations_reached": False
                    }

                # --- Execute tool calls ---
                assistant_message = {
                    "role": "assistant",
                    "content": message_text,
                    "tool_calls": tool_calls_from_model
                }
                messages_for_iterations.append(assistant_message)

                # Execute each tool and collect results
                tool_results = []
                for tc in tool_calls_from_model:
                    func = tc.get("function", {})
                    tool_name = func.get("name") or tc.get("name")
                    tool_call_id = tc.get("id")
                    raw_args = func.get("arguments", "{}")

                    # Parse arguments
                    if isinstance(raw_args, str):
                        try:
                            tool_args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            tool_args = {"_raw": raw_args}
                    elif isinstance(raw_args, dict):
                        tool_args = raw_args
                    else:
                        tool_args = {"_raw": str(raw_args)}

                    # Execute the tool
                    tool_result = tool_executor(tool_name, tool_args)

                    # Record for UI display
                    all_tool_calls.append({
                        "tool": tool_name,
                        "input": tool_args,
                        "result": tool_result,
                        "iteration": iteration
                    })

                    # Format tool result for the model
                    if not tool_result.get("success", True):
                        consecutive_errors += 1
                    else:
                        consecutive_errors = 0
                    
                    tool_result_content = self._format_tool_result(tool_result)

                    tool_results.append({
                        "tool_call_id": tool_call_id,
                        "name": tool_name,
                        "content": tool_result_content
                    })

                # Add all tool results as separate messages
                for tr in tool_results:
                    messages_for_iterations.append({
                        "role": "tool",
                        "tool_call_id": tr["tool_call_id"],
                        "name": tr["name"],
                        "content": tr["content"]
                    })

            except requests.exceptions.HTTPError as e:
                status = getattr(e.response, "status_code", None)
                if status in (401, 403):
                    raise Exception(f"Authentication failed: {e}")
                if status == 429:
                    raise Exception(f"Rate limit exceeded: {e}")
                raise Exception(f"API error (HTTP {status}): {e}")

            except requests.exceptions.Timeout:
                raise Exception("Request timed out after 300 seconds")

            except Exception as e:
                raise Exception(f"LLM error: {str(e)}")

        # --- Max iterations reached ---
        final_text = "\n\n".join(accumulated_text) if accumulated_text else ""
        if not final_text:
            final_text = "I've reached the maximum number of tool calling iterations. Please review the results above or provide additional guidance."
        
        return {
            "content": [{"type": "text", "text": final_text}],
            "usage": {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens
            },
            "tool_calls": all_tool_calls,
            "stop_reason": "max_iterations",
            "max_iterations_reached": True
        }
