# Gemini-Databricks AI Chatbot POC

A **code-first AI chatbot** built with Streamlit that runs LLM-powered conversations with persistent, sandboxed Python code execution inside Docker containers. The project supports multiple LLM backends — Google Gemini (direct API), Anthropic Claude, and Gemini-via-Databricks Model Serving — and provides an agentic tool-calling loop that lets the model create files, execute code, run shell commands, and persist outputs for the user to download.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Project Structure](#project-structure)
3. [Features](#features)
4. [Prerequisites](#prerequisites)
5. [Installation](#installation)
6. [Configuration](#configuration)
7. [Environment Variables](#environment-variables)
8. [Running the Application](#running-the-application)
9. [LLM Backends](#llm-backends)
10. [Databricks Model Serving Wrapper (`db_api.py`)](#databricks-model-serving-wrapper-db_apipy)
11. [Tool System & Agentic Loop](#tool-system--agentic-loop)
12. [Code Execution Sandbox](#code-execution-sandbox)
13. [File Handling Pipeline](#file-handling-pipeline)
14. [Database Schema](#database-schema)
15. [UI Components](#ui-components)
16. [Error Handling & Loop Protection](#error-handling--loop-protection)
17. [Cost Tracking](#cost-tracking)
18. [Known Limitations](#known-limitations)
19. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                     Streamlit Frontend (app.py)                  │
│  ┌──────────┐  ┌───────────┐  ┌───────────┐  ┌──────────────┐  │
│  │  Sidebar  │  │  Chat UI  │  │  File     │  │  Debug Mode  │  │
│  │  (Chats)  │  │ (Messages)│  │  Upload   │  │  (Timeline)  │  │
│  └──────────┘  └───────────┘  └───────────┘  └──────────────┘  │
└────────────────────────┬─────────────────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │   LLMClient         │  ← OpenAI-compatible format
              │   (llm_client.py)   │
              └──────────┬──────────┘
                         │  HTTP POST to Databricks Serving Endpoint
              ┌──────────▼──────────┐
              │  Databricks Model   │
              │  Serving Endpoint   │
              │  ┌────────────────┐ │
              │  │ db_api.py      │ │  ← MLflow PyFunc wrapper
              │  │ (GeminiFlash   │ │     OpenAI → Gemini → OpenAI
              │  │  Wrapper)      │ │
              │  └───────┬────────┘ │
              └──────────┼──────────┘
                         │
              ┌──────────▼──────────┐
              │   Google Gemini API │  (gemini-3-flash-preview, etc.)
              └─────────────────────┘

Alternative direct backends (bypass Databricks):
  - GeminiClient  (src/gemini_client.py) → Google genai SDK direct
  - ClaudeClient  (src/claude_client.py) → Anthropic SDK direct
```

### Data Flow

1. User types a message or uploads a file in the Streamlit UI.
2. Files are saved to disk **and** automatically copied into the Docker sandbox at `/home/user/uploads/`.
3. The message (with file references or inline content) is sent to the active LLM backend.
4. The LLM may return tool calls (`execute_python`, `create_file`, `execute_bash`, etc.).
5. The tool executor runs those operations inside the persistent Docker container.
6. Tool results are sent back to the LLM for the next iteration.
7. This agentic loop repeats until the LLM returns a final text response or the iteration limit is reached.
8. The assistant's response (including all tool call display blocks) is persisted to SQLite and rendered in the chat.

---

## Project Structure

```
gemini-databricks-main/
├── app.py                  # Main Streamlit application — UI, routing, message handling
├── db_api.py               # MLflow PyFunc wrapper: deploys Gemini behind a Databricks
│                           #   Model Serving endpoint with an OpenAI-compatible interface
├── config.yaml             # All application configuration (model, tokens, files, UI, costs, Docker)
├── requirements.txt        # Python dependencies
├── .gitignore              # Ignores .env, venv, __pycache__, data/
│
└── src/
    ├── __init__.py          # Package init, version = 0.1.0
    ├── llm_client.py        # Primary LLM client — talks to Databricks serving endpoint
    │                        #   over HTTP; handles OpenAI-format conversion, agentic tool
    │                        #   loop, context trimming, error tracking, title generation
    ├── gemini_client.py     # Alternative direct Gemini client using google-genai SDK;
    │                        #   loop protection, error escalation, thinking mode support
    ├── claude_client.py     # Alternative direct Claude client using Anthropic SDK;
    │                        #   exponential backoff with jitter, extended thinking support
    ├── code_executor.py     # Docker-based sandboxed code execution engine;
    │                        #   manages container lifecycle, file I/O, Python/Bash execution,
    │                        #   binary-aware file persistence (save_files)
    ├── database.py          # SQLAlchemy models & Database class — SQLite persistence for
    │                        #   chats, messages, files, thinking events, tool calls, sandbox files
    ├── file_handler.py      # File upload processing — saves to disk, converts PDFs/images/text
    │                        #   to base64 or inline text for LLM consumption
    └── utils.py             # Utility functions — token estimation, cost calculation,
                             #   context window management, formatting helpers
```

---

## Features

### Multi-Backend LLM Support
- **Databricks Model Serving** (`LLMClient` in `llm_client.py`): Primary production path. Sends OpenAI-format requests to a Databricks endpoint running the `GeminiFlashWrapper` MLflow model.
- **Direct Gemini** (`GeminiClient` in `gemini_client.py`): Uses the `google-genai` SDK directly. Supports function calling, thinking mode, and thought signatures for Gemini 3.
- **Direct Claude** (`ClaudeClient` in `claude_client.py`): Uses the Anthropic SDK. Supports extended thinking and has built-in retry with exponential backoff and jitter for rate limits.

### Code-First Sandbox Execution
- Persistent Docker containers per chat session (UBI9 Python 3.11/3.12 base image).
- Six tools exposed to the LLM: `create_file`, `read_file`, `list_files`, `execute_python`, `execute_bash`, `save_files`.
- Automatic Python syntax checking on file creation (AST parsing with context-aware error display).
- Binary file support — Excel, PDF, images, archives are base64-encoded for persistent storage.
- Uploaded files are auto-copied to `/home/user/uploads/` inside the sandbox.

### Intelligent File Routing
- **Data files** (CSV, XLSX, JSON, Parquet, etc.) are routed through the sandbox for code-based processing — never embedded inline.
- **Small text files** (< 50 KB) like `.py`, `.md`, `.txt` are embedded directly in the message context.
- **PDFs and images** are base64-encoded and sent to models that support multimodal input.
- Files larger than `INLINE_SIZE_THRESHOLD` (50 KB) are always routed through the sandbox regardless of type.

### Persistent Chat & Project Files
- SQLite database stores full conversation history, file metadata, thinking events, and tool call logs.
- Sandbox files can be saved to the database for download even after the Docker container expires.
- Project Files sidebar shows a file tree with preview and download buttons, supporting both text and binary files.

### Agentic Tool Loop with Safety Controls
- Configurable maximum iterations (default: 30) to prevent runaway loops.
- Error signature deduplication — detects when the model is repeating the same failed fix.
- Escalating error context messages guide the model to stop retrying after 2–3 failures.
- Unsolvable error detection (API keys, network, auth, permissions) short-circuits retry attempts.
- Context trimming compresses older tool exchange rounds to prevent token bloat across many iterations.

### Debug Mode
- Toggle in the sidebar to show sandbox container IDs, recent tool calls with execution times, and status indicators.
- Execution Timeline expander on each message shows chronological thinking events and tool calls.

---

## Prerequisites

- **Python 3.10+**
- **Docker** — must be running and accessible to the current user (the app creates and manages containers).
- **Databricks workspace** (if using the Databricks Model Serving path) with a deployed endpoint running `db_api.py`.
- **API keys** — at least one of: Databricks personal access token, Google Gemini API key, or Anthropic API key.

---

## Installation

```bash
# Clone the repository
git clone <repo-url>
cd gemini-databricks-main

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Linux/Mac
# .venv\Scripts\activate    # Windows

# Install dependencies
pip install -r requirements.txt
```

### Dependencies

| Package | Purpose |
|---|---|
| `streamlit>=1.28.0` | Web UI framework |
| `openai>=1.0.0` | OpenAI-compatible client (used indirectly by LLMClient for format) |
| `sqlalchemy>=2.0.0` | ORM for SQLite database |
| `pyyaml>=6.0` | Configuration file parsing |
| `python-dotenv>=1.0.0` | `.env` file loading |
| `pymupdf>=1.23.0` | PDF text/image extraction (imported as `fitz`) |
| `pillow>=10.0.0` | Image processing |
| `docker>=7.0.0` | Docker container management |
| `requests` | HTTP client for Databricks endpoint |

Additional dependencies for the Databricks wrapper (`db_api.py`):

| Package | Purpose |
|---|---|
| `mlflow` | Model logging and serving framework |
| `google-genai>=1.0.0` | Google Gemini SDK (2025 GA version, not the deprecated `google-generativeai`) |
| `numpy` | Array handling for MLflow input unwrapping |

---

## Configuration

All configuration lives in `config.yaml`:

```yaml
llm:
  model: "gemini-3-flash-preview"    # Model name sent to the backend
  max_tokens: 65536                   # Max output tokens per response
  endpoint_name: "gemini-lakehouse"   # Databricks serving endpoint name

context:
  max_tokens: 65536                   # Context window budget
  token_estimation_ratio: 4           # Characters per token (rough heuristic)

files:
  max_size_mb: 50                     # Maximum upload size
  allowed_extensions: [pdf, txt, md, py, js, json, csv, png, jpg, jpeg,
                       webp, xlsx, xls, tsv, parquet, xml, sql, yaml,
                       yml, html, css, sh]

ui:
  page_title: "Claude Chatbot POC"
  page_icon: "🤖"
  show_thinking_by_default: false
  auto_title_generation: false

costs:
  input_cost_per_million: 3.00        # $/M input tokens
  output_cost_per_million: 15.00      # $/M output tokens
  show_cost_estimate: true

database:
  path: "data/chats.db"              # SQLite database location

storage:
  uploads_dir: "data/uploads"        # Host-side file upload directory

code_execution:
  enabled: true
  max_iterations: 30                  # Max agentic tool loop iterations
  timeout_seconds: 120                # Per-execution timeout

  docker:
    image: "registry.access.redhat.com/ubi9/python-312"
    memory_limit: "2048m"             # Note: CodeExecutor currently uses 512m
    cpu_limit: 0.5
    workspace_base: null              # null = use system temp directory

  ui:
    show_thinking: true
    show_code_attempts: true
    show_execution_details: true
```

> **Note**: The `docker.memory_limit` in config (2048m) differs from the hardcoded 512m in `code_executor.py`. The CodeExecutor currently uses its own hardcoded limit. If you need to change container memory, edit `CodeExecutor.get_or_create_sandbox()`.

---

## Environment Variables

Create a `.env` file in the project root:

```bash
# Required — Databricks Model Serving path
LLM_API_KEY=dapi...                          # Databricks personal access token
LLM_BASE_URL=https://<workspace>.cloud.databricks.com  # Databricks workspace URL

# Required for db_api.py (Databricks-hosted wrapper)
GEMINI_API_KEY=AIza...                       # Google Gemini API key

# Alternative — Direct Gemini (for gemini_client.py)
# GEMINI_API_KEY=AIza...

# Alternative — Direct Claude (for claude_client.py)
# ANTHROPIC_API_KEY=sk-ant-...
```

---

## Running the Application

```bash
# Ensure Docker is running
docker info

# Start the Streamlit app
streamlit run app.py
```

The app opens at `http://localhost:8501`. On first launch it:
1. Creates the SQLite database at `data/chats.db`.
2. Initializes a default chat session.
3. Pulls the Docker image (`registry.access.redhat.com/ubi9/python-311`) if not already present.

---

## LLM Backends

### 1. Databricks Model Serving (Production Path)

**Client**: `LLMClient` (`src/llm_client.py`)

This is the default and primary path used by `app.py`. It:
- Sends requests to `{LLM_BASE_URL}/serving-endpoints/{endpoint_name}/invocations`.
- Wraps messages in `{"inputs": [{ ... }]}` — the standard MLflow serving payload format.
- Includes a system prompt with detailed code-first instructions.
- Defines six tools in OpenAI function-calling format.
- Handles the full agentic loop: tool calls → execution → results → next iteration.
- Implements context trimming (`_trim_old_tool_results`) that collapses old tool exchange rounds into compact summaries to prevent context window exhaustion across many iterations.
- Generates chat titles via a separate lightweight LLM call.

**Key methods**:
- `send_message()` — Main agentic loop. Accepts a `tool_executor` callback and loops up to `max_iterations`.
- `_convert_messages()` — Converts internal rich message format (with `sandbox_file_ref`, `document`, `image`, `tool_result`, `tool_result_display` blocks) into flat OpenAI messages.
- `_trim_old_tool_results()` — After iteration 5, compresses older rounds by replacing (assistant + tool) message pairs with a single summary message. Preserves recent rounds for model context.
- `_format_tool_result()` — Truncates tool output to 8,000 chars (keeping beginning and end) to prevent context bloat.
- `generate_title()` — Calls the LLM with a short prompt to auto-generate a chat title from the first user message.

### 2. Direct Gemini

**Client**: `GeminiClient` (`src/gemini_client.py`)

Uses the `google-genai` SDK directly (the 2025 GA `google.genai` package, **not** the deprecated `google-generativeai`). This client:
- Creates a chat session via `client.chats.create()` with the full message history.
- Defines tools using `types.FunctionDeclaration` with `parameters_json_schema`.
- Disables automatic function calling (`types.AutomaticFunctionCallingConfig(disable=True)`) so the client manages the loop.
- Supports thinking mode via `types.ThinkingConfig(include_thoughts=True)`.
- Converts Gemini response parts (text, thinking, function_call) back into Claude-compatible content blocks for UI rendering.

### 3. Direct Claude

**Client**: `ClaudeClient` (`src/claude_client.py`)

Uses the Anthropic Python SDK. This client:
- Defines tools using Claude's native `input_schema` format.
- Supports extended thinking (`{"type": "enabled"}`).
- Implements `_make_request_with_retry()` with exponential backoff, jitter, and retry-after header support for rate limit handling (up to 3 retries, max 60s delay).
- Maps tool results back using Claude's `tool_result` content blocks with `is_error` flags.
- The tool set here includes `save_file` (singular, per-file) rather than `save_files` (batch), reflecting an earlier design.

---

## Databricks Model Serving Wrapper (`db_api.py`)

This file defines `GeminiFlashWrapper`, an MLflow `PythonModel` that acts as a **translation layer** between the OpenAI-compatible interface expected by Databricks Model Serving and the Google Gemini API.

### What It Does

1. **Receives** an OpenAI-compatible request (messages, tools, model, temperature, max_tokens) from the serving endpoint.
2. **Converts messages** to Gemini format:
   - `system` messages → `system_instruction` parameter.
   - `user` messages → `role="user"` with text parts.
   - `assistant` messages → `role="model"` with text and/or `FunctionCall` parts.
   - `tool` messages → `role="user"` with `FunctionResponse` parts.
3. **Converts tools** from OpenAI `{"type": "function", "function": {...}}` to Gemini `types.FunctionDeclaration`.
4. **Calls Gemini** via `client.models.generate_content()` (not `chats.create()`) to preserve raw dict parts that contain `thought_signature` fields needed for Gemini 3 function calling.
5. **Converts the response** back to OpenAI format with `choices`, `message`, `tool_calls`, `finish_reason`, and `usage`.

### Thought Signature Handling

Gemini 3 models require `thought_signature` bytes to be preserved and sent back in subsequent turns during multi-turn function calling. The wrapper:
- Extracts `thought_signature` from response parts as raw `bytes`.
- Base64-encodes them for safe JSON transport (stored as `_thought_signature` on tool call entries).
- On subsequent turns, decodes them back to `bytes` and injects them into the `function_call` part dict.
- Uses raw dicts instead of `types.Content` objects for model turns that contain signatures, because the SDK's type conversion strips these fields.

### Deploying to Databricks

```python
# Run db_api.py directly to register the model with MLflow
python db_api.py
# Output: "Gemini Flash wrapper registered. Run ID: <run_id>"
# Output: "Model URI: runs:/<run_id>/gemini_flash_wrapper"
```

Then deploy the registered model to a Databricks Model Serving endpoint. The endpoint name should match `config.yaml`'s `llm.endpoint_name` (default: `"gemini-lakehouse"`). Ensure `GEMINI_API_KEY` is set as an environment variable on the serving endpoint.

---

## Tool System & Agentic Loop

The LLM has access to six tools, defined in `LLMClient._get_tools()`:

| Tool | Description | Key Behavior |
|---|---|---|
| `create_file` | Create/overwrite a file at a path under `/home/user/` | Auto-runs AST syntax check for `.py` files; returns line-numbered preview of first 20 lines |
| `read_file` | Read a file's contents | For large data files, the system prompt instructs the model to use `execute_python` with pandas instead |
| `list_files` | Recursively list files in a directory | Returns full paths with file sizes; defaults to `/home/user` |
| `execute_python` | Run Python code inline or execute a `.py` file | Writes inline code to a temp file (`_tmp_exec.py`) to avoid shell escaping issues; cleans up after |
| `execute_bash` | Run a shell command | Used for `pip install` (with `--trusted-host` flags), `ls`, `head`, `grep`, etc. |
| `save_files` | Persist one or more files from sandbox to database | Supports batch saves; handles both text and binary (base64-encoded) files; max 10 MB per file |

### Agentic Loop Flow (LLMClient.send_message)

```
1. Build OpenAI messages + system prompt + tools
2. POST to Databricks endpoint
3. Parse response → extract tool_calls
4. If no tool_calls → return final text
5. For each tool_call:
   a. Parse arguments (JSON string or dict)
   b. Execute via tool_executor callback
   c. Record in all_tool_calls list
   d. Format result (truncated to 8KB max)
   e. Append tool result message
6. After iteration 5: compress old tool rounds
7. Increment iteration, go to step 2
8. If max_iterations reached → return accumulated text + warning
```

---

## Code Execution Sandbox

**Implementation**: `CodeExecutor` (`src/code_executor.py`)

### Container Lifecycle

- Each chat gets its own persistent Docker container.
- Containers are created on first tool call and reused for the entire chat session.
- The host workspace directory (under system temp or configured `workspace_base`) is bind-mounted to `/home/user` inside the container.
- Containers use `tail -f /dev/null` as the entrypoint to stay running.
- On chat deletion, the container is stopped and removed.
- `cleanup_orphaned_containers()` removes containers from previous runs by label (`chatbot-sandbox`).

### Container Configuration

| Parameter | Value |
|---|---|
| Base Image | `registry.access.redhat.com/ubi9/python-311` |
| Memory Limit | 512 MB |
| CPU Quota | 50% of one core |
| Network | Bridge mode (internet access for pip install) |
| Working Directory | `/home/user` |
| Persistence | Bind-mounted host directory |

### File Operations

- **create_file**: Writes to the host workspace, then ensures the container can see it via the bind mount. Returns a preview and optional syntax check.
- **read_file**: Reads from the host workspace. Path normalization ensures all paths map to `/home/user/...`.
- **list_files**: Uses Python's `pathlib.rglob()` on the host workspace.
- **upload_to_sandbox**: Copies host files into the workspace's `uploads/` subdirectory using `shutil.copy2`.

### Binary File Support

The `save_files` method detects binary files by extension (xlsx, pdf, png, jpg, zip, parquet, docx, etc.) and:
1. Reads them as raw bytes.
2. Base64-encodes for database storage.
3. Stores with `is_binary=1` flag.
4. On download (in sidebar), decodes back to bytes with correct MIME type.

---

## File Handling Pipeline

### Upload Flow

```
User uploads file via Streamlit
         │
         ▼
FileHandler.save_file()          → Save to data/uploads/{chat_id}/{filename}
         │
         ▼
_upload_file_to_sandbox()        → Copy to workspace/uploads/{filename}
         │                          (bind-mounted as /home/user/uploads/)
         ▼
_should_use_sandbox()?
  ├─ YES (csv, xlsx, json, parquet, large files)
  │     → Add sandbox_file_ref block to message
  │        "[📎 File uploaded to sandbox: X. Use code to process.]"
  │
  └─ NO (small text, pdf, image)
        → FileHandler.convert_to_claude_format()
           ├─ text → inline {"type": "text", "text": "..."}
           ├─ pdf  → base64 {"type": "document", "source": {"type": "base64", ...}}
           └─ image → base64 {"type": "image", "source": {"type": "base64", ...}}
```

### File Type Classification Constants (app.py)

- `CODE_PROCESSABLE_TYPES`: `{csv, xlsx, xls, json, xml, tsv, parquet, sql, db, sqlite}` — Always routed to sandbox.
- `INLINE_TEXT_TYPES`: `{txt, md, py, js, ts, html, css, yaml, yml, sh, bash, toml, ini, cfg, conf, env}` — Embedded inline if small enough.
- `INLINE_SIZE_THRESHOLD`: `50,000 bytes` — Files above this size are always routed to sandbox.

---

## Database Schema

**Engine**: SQLite via SQLAlchemy ORM  
**Location**: `data/chats.db` (configurable in `config.yaml`)

### Tables

#### `chats`
| Column | Type | Description |
|---|---|---|
| `id` | String (PK) | UUID |
| `title` | String | Chat title (auto-generated or "New Chat") |
| `created_at` | DateTime | Creation timestamp |
| `last_updated` | DateTime | Last activity timestamp |
| `message_count` | Integer | Running message count |
| `total_tokens` | Integer | Running token total |
| `sandbox_id` | String (nullable) | Docker container ID for this chat |

#### `messages`
| Column | Type | Description |
|---|---|---|
| `id` | String (PK) | UUID |
| `chat_id` | String (FK → chats) | Parent chat |
| `role` | String | `"user"` or `"assistant"` |
| `content` | JSON | Full message content — can be a string or list of content blocks (text, tool_use, tool_result_display, thinking, sandbox_file_ref, document, image) |
| `timestamp` | DateTime | Message timestamp |
| `token_count` | Integer | Estimated token count |

#### `files`
| Column | Type | Description |
|---|---|---|
| `id` | String (PK) | UUID |
| `chat_id` | String (FK → chats) | Parent chat |
| `filename` | String | Original filename |
| `file_path` | String | Host filesystem path |
| `file_type` | String | Extension (e.g., `"csv"`, `"pdf"`) |
| `size_bytes` | Integer | File size |
| `in_context` | Boolean | Whether file is included in LLM context |
| `token_estimate` | Integer | Estimated tokens for context budgeting |
| `uploaded_at` | DateTime | Upload timestamp |

#### `thinking_events`
| Column | Type | Description |
|---|---|---|
| `id` | Integer (PK) | Auto-increment |
| `chat_id` | String (FK → chats) | Parent chat |
| `message_id` | String (nullable) | Triggering message ID |
| `timestamp` | DateTime | Event timestamp |
| `thinking_text` | String | Model's thinking content |
| `signature` | String (nullable) | Thinking signature (Claude) |
| `iteration` | Integer | Agentic loop iteration number |

#### `tool_calls`
| Column | Type | Description |
|---|---|---|
| `id` | Integer (PK) | Auto-increment |
| `chat_id` | String (FK → chats) | Parent chat |
| `message_id` | String (nullable) | Triggering message ID |
| `timestamp` | DateTime | Call timestamp |
| `iteration` | Integer | Loop iteration |
| `tool_name` | String | Tool that was called |
| `tool_input` | JSON | Arguments passed to the tool |
| `status` | String | `"pending"`, `"success"`, `"error"`, or `"partial"` |
| `tool_output` | JSON (nullable) | Structured result data |
| `error_msg` | String (nullable) | Error message if failed |
| `sandbox_id` | String (nullable) | Container ID used |
| `execution_time_ms` | Integer (nullable) | Execution duration in ms |

#### `sandbox_files`
| Column | Type | Description |
|---|---|---|
| `id` | String (PK) | UUID |
| `chat_id` | String (FK → chats) | Parent chat |
| `filepath` | String | Full path (e.g., `"/home/user/calculator/main.py"`) |
| `filename` | String | Base filename (e.g., `"main.py"`) |
| `directory` | String (nullable) | Directory portion (e.g., `"calculator/"`) |
| `content` | String | File contents — plain text or base64-encoded binary |
| `description` | String (nullable) | Description from the model |
| `is_binary` | Integer | `0` for text, `1` for base64-encoded binary |
| `file_type` | String (nullable) | Detected type (e.g., `"python"`, `"excel"`) |
| `size_bytes` | Integer (nullable) | File size |
| `created_at` | DateTime | Creation timestamp |
| `updated_at` | DateTime | Last update timestamp |

All tables use cascade delete through the `chats` parent — deleting a chat removes all associated messages, files, events, and sandbox files.

---

## UI Components

### Sidebar
- **Chat List**: All chats sorted by last updated. Active chat shows message count, timestamp, and an expandable Project Files section.
- **Project Files**: File tree grouped by directory. Each file has a preview button (👁️) and download button (⬇️). Binary files show a download-only indicator. Text files display in a code block with copy button.
- **Debug Mode**: Checkbox that reveals sandbox container IDs and recent tool call logs with execution times and status.

### Main Chat Area
- **Chat Header**: Title + delete button + cost/token estimate.
- **Message Rendering**: User messages show file attachment badges (📄 PDF, 🖼️ Image, 📂 Sandbox File, 📎 Inline File). Assistant messages render thinking blocks (collapsible), code execution blocks (expandable with syntax-highlighted code), tool result displays (per-tool type rendering), and markdown text.
- **Execution Timeline**: Collapsible section per message showing chronological thinking events and tool calls with icons, status, and timing.
- **Chat Input**: Standard `st.chat_input` at the bottom.

### File Upload Panel
- Right column with file uploader. Shows routing decision per file (sandbox vs. message context).
- Files in the chat are listed with context status, token estimates, and delete buttons.

### Code Block Copy Button
The `code_block_with_copy()` utility creates code blocks with a clipboard copy button using unique keys (MD5 hash + UUID) to avoid Streamlit key collisions.

---

## Error Handling & Loop Protection

### Error Classification

Both `LLMClient` and `GeminiClient` classify errors into categories:

| Category | Patterns | Action |
|---|---|---|
| **Unsolvable/Environmental** | `api_key`, `authentication`, `403`, `401`, `quota exceeded`, `rate limit`, `permission denied`, `connection refused`, `dns`, `ssl` | Immediately stop retrying; instruct model to explain the issue to the user |
| **Transient** | `timeout`, `503`, `500`, `502` | May be worth retrying |
| **Code errors** | Everything else (syntax errors, import errors, runtime exceptions) | Normal retry with escalating urgency |

### Error Signature Deduplication

Errors are normalized into signatures by:
1. Taking the first line (100 chars max).
2. Replacing line numbers with `N`, file paths with `FILE`, hex addresses with `ADDR`.
3. Counting occurrences per signature.

### Escalation Levels

| Count | Behavior |
|---|---|
| 1st occurrence | Normal error message |
| 2nd occurrence | Warning: "⚠️ SECOND OCCURRENCE — if next attempt fails, STOP" |
| 3rd+ occurrence | Hard stop: "🛑 REPEATED ERROR — STOP RETRYING. Summarize and ask user." |
| Approaching iteration limit | Warning: "⚠️ APPROACHING ITERATION LIMIT — wrap up or explain" |
| 5 consecutive errors (Gemini) | Force-injects a STOP message into the function responses |

### Claude-Specific Rate Limit Handling

`ClaudeClient._make_request_with_retry()` implements:
- Up to 3 retries with exponential backoff (1s → 2s → 4s base).
- ±25% jitter to prevent thundering herd.
- Respects server `retry-after` header if present.
- Max delay capped at 60s.

### Context Trimming (LLMClient)

`_trim_old_tool_results()` activates after iteration 5 and:
1. Identifies "rounds" — an assistant message with tool_calls followed by its matching tool results.
2. Keeps the last N/2 rounds detailed (default: keep last 4 rounds).
3. Collapses earlier rounds into a single assistant message: `"[Previous tool round] create_file(/home/user/x.py, 500 chars) | → Output preview..."`.
4. Processes in reverse index order to maintain valid indices.

This prevents the context window from filling up with old code execution results during long multi-step tasks.

---

## Cost Tracking

The UI displays estimated costs per chat based on:
- **Input cost**: `$3.00` per million tokens (configurable).
- **Output cost**: `$15.00` per million tokens (configurable).
- Token counts are estimated using a simple heuristic: `chars / 4` for text, 750 tokens for images, 1000 tokens for PDFs, 50 tokens for sandbox file references.
- The display shows total tokens and estimated cost in the chat header.

---

## Known Limitations

1. **Container memory**: `code_executor.py` hardcodes 512 MB per container. The `config.yaml` value of 2048 MB is not read by the executor.
2. **No streaming**: All LLM responses are returned as complete responses, not streamed. Long-running generations show a spinner.
3. **Single-user**: The app uses Streamlit session state. Multiple users sharing the same instance may have state conflicts.
4. **No auth**: The Streamlit app itself has no authentication. Rely on network-level access control.
5. **Gemini client uses `chats.create()`**: The `GeminiClient` uses the chat API which may strip `thought_signature` fields. The Databricks wrapper (`db_api.py`) avoids this by using `models.generate_content()` directly.
6. **Token estimation is rough**: The `chars / 4` heuristic can be significantly off for code, non-English text, or base64 content.
7. **Container cleanup**: Orphaned containers from crashed sessions persist until `cleanup_orphaned_containers()` is called. Consider adding a startup cleanup call.
8. **SQLAlchemy session management**: Some queries use `session.expunge_all()` which detaches all objects. This works but could be fragile with lazy-loaded relationships.
9. **Tool mismatch**: `ClaudeClient` defines `save_file` (singular), while `LLMClient` and `CodeExecutor` use `save_files` (batch). The active path (`LLMClient`) uses the batch version.
10. **SSL verification disabled**: `LLMClient` uses `verify=False` for Databricks requests and suppresses urllib3 warnings. This is typical for internal Databricks deployments but should be enabled in production if possible.

---

## Troubleshooting

### Docker Issues

```bash
# Check Docker is running
docker info

# Check for orphaned sandbox containers
docker ps -a --filter "label=chatbot-sandbox"

# Clean up orphaned containers
docker rm -f $(docker ps -a -q --filter "label=chatbot-sandbox")

# Pull the required image manually
docker pull registry.access.redhat.com/ubi9/python-311
```

### Database Issues

```bash
# Reset the database (deletes all chat history)
rm data/chats.db

# The app will recreate it on next launch
```

### Common Errors

| Error | Cause | Fix |
|---|---|---|
| `LLM_API_KEY not found` | Missing `.env` file or `LLM_API_KEY` not set | Create `.env` with your Databricks PAT |
| `Failed to connect to Docker` | Docker daemon not running | Start Docker Desktop or `systemctl start docker` |
| `Image not found` | Docker image not pulled | Run `docker pull registry.access.redhat.com/ubi9/python-311` |
| `Authentication failed` | Invalid Databricks token | Regenerate your PAT in Databricks workspace settings |
| `GEMINI_API_KEY not found` | Only applies to `db_api.py` on the Databricks endpoint | Set the env var in the Databricks serving endpoint configuration |

---

## License

Not specified. Contact the repository owner for licensing information.
