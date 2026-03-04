/**
 * render.js — Build tool-result cards, inline HTML outputs, and other UI blocks.
 *
 * All functions return DOM elements. They don't append to the page — the caller
 * (chat.js) decides where to put them.
 */

const Render = {
    /** Tool name → icon mapping */
    TOOL_ICONS: {
        create_file:    '📝',
        read_file:      '📖',
        list_files:     '📂',
        execute_python: '▶️',
        execute_bash:   '🖥️',
        save_files:     '💾',
        health_check:   '🔌',
        list_schemas:   '📂',
        list_tables:    '📋',
        describe_table: '🔍',
        run_query:      '🔎',
    },

    /**
     * Create a tool card in "running" state.
     * Returns the card element. Call updateToolCard() when done.
     */
    createToolCard(data) {
        const card = document.createElement('div');
        card.className = 'tool-card running';
        card.dataset.iteration = data.iteration;
        card.dataset.tool = data.tool;

        const icon = this.TOOL_ICONS[data.tool] || '⚙️';
        const meta = this._toolMeta(data.tool, data.input);

        card.innerHTML = `
            <div class="tool-card-header" onclick="this.parentElement.classList.toggle('open')">
                <span class="tool-card-icon">${icon}</span>
                <span class="tool-card-name">${this._esc(data.tool)}</span>
                <span class="tool-card-meta">${this._esc(meta)}</span>
                <span class="tool-card-status running">running</span>
                <span class="tool-card-chevron">▸</span>
            </div>
            <div class="tool-card-body">
                ${this._renderInput(data.tool, data.input)}
            </div>
        `;

        return card;
    },

    /**
     * Update an existing tool card with the result.
     */
    updateToolCard(card, data) {
        const success = data.success;
        card.className = `tool-card ${success ? 'success' : 'error'}`;

        // Update status badge
        const badge = card.querySelector('.tool-card-status');
        if (badge) {
            badge.className = `tool-card-status ${success ? 'success' : 'error'}`;
            badge.textContent = success ? 'done' : 'error';
        }

        // Append output to body
        const body = card.querySelector('.tool-card-body');
        if (body) {
            if (data.output) {
                body.innerHTML += `
                    <div class="tool-card-label">Output</div>
                    <pre>${this._esc(data.output)}</pre>
                `;
            }
            if (data.error) {
                body.innerHTML += `
                    <div class="tool-card-error">⚠ ${this._esc(data.error)}</div>
                `;
            }
        }

        // Auto-expand on error
        if (!success) card.classList.add('open');
    },

    /**
     * Render an inline HTML output (chart or table) in an iframe.
     * Uses a URL to load from the server — avoids SSE payload issues.
     */
    createHtmlOutput(data) {
        const container = document.createElement('div');
        container.className = 'html-output';

        const kindIcon = data.kind === 'chart' ? '📊' :
                         data.kind === 'table' ? '📋' : '📄';
        const height = data.kind === 'chart' ? 500 :
                       data.kind === 'table' ? 400 : 350;

        container.innerHTML = `
            <div class="html-output-label">${kindIcon} ${this._esc(data.path)}</div>
        `;

        const iframe = document.createElement('iframe');
        iframe.style.height = height + 'px';

        if (data.url) {
            // Load from server endpoint (preferred — no SSE size limits)
            iframe.src = data.url;
        } else if (data.html) {
            // Fallback: inline HTML via srcdoc
            iframe.sandbox = 'allow-scripts allow-same-origin';
            iframe.srcdoc = data.html;
        }

        container.appendChild(iframe);
        return container;
    },

    /**
     * Create a collapsible thinking block.
     */
    createThinkingBlock(text) {
        const block = document.createElement('div');
        block.className = 'thinking-block';
        block.innerHTML = `
            <button class="thinking-toggle" onclick="this.parentElement.classList.toggle('open')">
                🤔 <span>View thinking process</span>
            </button>
            <div class="thinking-content">${this._esc(text)}</div>
        `;
        return block;
    },

    /**
     * Create an error banner.
     */
    createErrorBanner(message) {
        const div = document.createElement('div');
        div.className = 'error-banner';
        div.textContent = `Error: ${message}`;
        return div;
    },

    /**
     * Create the typing indicator.
     */
    createTypingIndicator() {
        const div = document.createElement('div');
        div.className = 'typing-indicator';
        div.id = 'typing';
        div.innerHTML = `
            <div class="typing-dots">
                <span></span><span></span><span></span>
            </div>
            <span class="typing-label">Thinking…</span>
        `;
        return div;
    },

    /**
     * Update typing indicator label (e.g., "Running list_schemas…")
     */
    updateTypingLabel(text) {
        const label = document.querySelector('#typing .typing-label');
        if (label) label.textContent = text;
    },

    /** Remove typing indicator */
    removeTyping() {
        const el = document.getElementById('typing');
        if (el) el.remove();
    },

    /**
     * Create the collapsible execution steps container.
     * Tool cards go inside this. Charts and text stay outside.
     */
    createExecutionSteps() {
        const container = document.createElement('div');
        container.className = 'execution-steps';
        container.innerHTML = `
            <button class="execution-steps-toggle" onclick="this.parentElement.classList.toggle('open')">
                <span class="execution-steps-chevron">▸</span>
                <span>Execution steps</span>
                <span class="execution-steps-count">0</span>
            </button>
            <div class="execution-steps-body"></div>
        `;
        return container;
    },

    /**
     * Update the step count badge on the execution steps container.
     */
    updateStepCount(stepsContainer, count) {
        const badge = stepsContainer.querySelector('.execution-steps-count');
        if (badge) badge.textContent = count;
    },

    // ── Private helpers ──────────────────────────────────────────────

    _toolMeta(tool, input) {
        switch (tool) {
            case 'run_query':
                return (input.sql || '').slice(0, 60) + ((input.sql || '').length > 60 ? '…' : '');
            case 'list_tables':
                return input.schema || '';
            case 'describe_table':
                return `${input.schema || ''}.${input.table || ''}`;
            case 'execute_python':
                return input.file_path || 'inline code';
            case 'execute_bash':
                return (input.command || '').slice(0, 50);
            case 'create_file':
                return input.path || '';
            case 'read_file':
                return input.path || '';
            case 'save_files':
                return `${(input.files || []).length} file(s)`;
            default:
                return '';
        }
    },

    _renderInput(tool, input) {
        if (tool === 'run_query' && input.sql) {
            return `<div class="tool-card-label">SQL</div><pre>${this._esc(input.sql)}</pre>`;
        }
        if (tool === 'execute_python' && input.code) {
            return `<div class="tool-card-label">Code</div><pre>${this._esc(input.code)}</pre>`;
        }
        if (tool === 'execute_bash' && input.command) {
            return `<div class="tool-card-label">Command</div><pre>${this._esc(input.command)}</pre>`;
        }
        if (tool === 'create_file') {
            let html = `<div class="tool-card-label">Path</div><pre>${this._esc(input.path || '')}</pre>`;
            if (input.content) {
                const preview = input.content.length > 300
                    ? input.content.slice(0, 300) + '…'
                    : input.content;
                html += `<div class="tool-card-label">Content</div><pre>${this._esc(preview)}</pre>`;
            }
            return html;
        }
        // Default: show all params
        const pairs = Object.entries(input)
            .filter(([k, v]) => v !== undefined && v !== null)
            .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`);
        if (pairs.length === 0) return '';
        return `<pre>${this._esc(pairs.join('\n'))}</pre>`;
    },

    _esc(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = String(str);
        return div.innerHTML;
    },
};
