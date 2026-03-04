/**
 * chat.js — Message rendering and the send flow.
 *
 * Uses marked.js (loaded from CDN in index.html) for markdown → HTML.
 */

const Chat = {
    chatId: null,
    isSending: false,
    fileIds: [],       // IDs of files attached to next message

    /** Load a chat and render all existing messages. */
    async load(chatId) {
        this.chatId = chatId;
        this.fileIds = [];
        Files.clear();

        const messagesEl = document.getElementById('messages');

        if (!chatId) {
            messagesEl.innerHTML = `
                <div class="empty-state">
                    <h2>LH Genie</h2>
                    <p>Ask questions about your data in natural language.
                    Create a new chat to get started.</p>
                </div>
            `;
            document.getElementById('chat-title').textContent = 'Select or create a chat';
            this._toggleInput(false);
            return;
        }

        this._toggleInput(true);

        try {
            const chat = await API.getChat(chatId);
            document.getElementById('chat-title').textContent = chat.title || 'New Chat';
            messagesEl.innerHTML = '';

            for (const msg of chat.messages) {
                this._renderMessage(msg.role, msg.content);
            }

            this._scrollToBottom();
        } catch (err) {
            messagesEl.innerHTML = '';
            messagesEl.appendChild(Render.createErrorBanner(err.message));
        }
    },

    /** Send the current input and stream the response. */
    async send() {
        const input = document.getElementById('user-input');
        const text = input.value.trim();
        if (!text || !this.chatId || this.isSending) return;

        this.isSending = true;
        input.value = '';
        input.style.height = 'auto';
        this._updateSendBtn();

        // Render user message immediately
        const messagesEl = document.getElementById('messages');
        this._appendUserMessage(text);

        // Start assistant message container
        const assistantDiv = this._startAssistantMessage();
        const toolsContainer = assistantDiv.querySelector('.assistant-tools');
        const textContainer = assistantDiv.querySelector('.assistant-text');

        // Show typing indicator
        messagesEl.appendChild(Render.createTypingIndicator());
        this._scrollToBottom();

        // Track tool cards by iteration for updating
        const toolCards = {};

        const fileIds = [...this.fileIds];
        this.fileIds = [];
        Files.clear();

        try {
            await Stream.send(this.chatId, text, fileIds, {
                status: (data) => {
                    Render.updateTypingLabel('Thinking…');
                },

                tool_start: (data) => {
                    Render.updateTypingLabel(`Running ${data.tool}…`);
                    const card = Render.createToolCard(data);
                    toolCards[data.iteration] = card;
                    toolsContainer.appendChild(card);
                    this._scrollToBottom();
                },

                tool_result: (data) => {
                    const card = toolCards[data.iteration];
                    if (card) {
                        Render.updateToolCard(card, data);
                    }
                    this._scrollToBottom();
                },

                html_output: (data) => {
                    const output = Render.createHtmlOutput(data);
                    toolsContainer.appendChild(output);
                    this._scrollToBottom();
                },

                text: (data) => {
                    Render.removeTyping();
                    const html = this._renderMarkdown(data.text);
                    // Append to existing text (model may emit multiple text events)
                    textContainer.innerHTML += html;
                    this._scrollToBottom();
                },

                thinking: (data) => {
                    if (data.text) {
                        const block = Render.createThinkingBlock(data.text);
                        toolsContainer.appendChild(block);
                    }
                },

                error: (data) => {
                    Render.removeTyping();
                    textContainer.appendChild(Render.createErrorBanner(data.message));
                    this._scrollToBottom();
                },

                done: (data) => {
                    Render.removeTyping();
                    this._scrollToBottom();

                    // Refresh sidebar to pick up auto-title
                    Sidebar.refresh();
                },
            });
        } catch (err) {
            Render.removeTyping();
            textContainer.appendChild(Render.createErrorBanner(err.message));
        }

        this.isSending = false;
        this._updateSendBtn();
        document.getElementById('user-input').focus();
    },

    // ── Private helpers ──────────────────────────────────────────────

    /** Render a single message (from DB or freshly sent). */
    _renderMessage(role, content) {
        const messagesEl = document.getElementById('messages');

        if (role === 'user') {
            const text = this._extractText(content);
            if (text) this._appendUserMessage(text);
            return;
        }

        // Assistant message — may contain text + tool_result_display blocks
        if (role === 'assistant') {
            const div = this._startAssistantMessage();
            const toolsContainer = div.querySelector('.assistant-tools');
            const textContainer = div.querySelector('.assistant-text');

            if (Array.isArray(content)) {
                for (const block of content) {
                    if (block.type === 'text' && block.text) {
                        textContainer.innerHTML += this._renderMarkdown(block.text);
                    } else if (block.type === 'tool_result_display') {
                        const card = Render.createToolCard({
                            tool: block.tool_name,
                            input: block.tool_input || {},
                            iteration: block.iteration || 0,
                        });
                        Render.updateToolCard(card, {
                            success: block.result?.success ?? true,
                            output: block.result?.output || '',
                            error: block.result?.error || null,
                        });
                        toolsContainer.appendChild(card);
                    } else if (block.type === 'thinking') {
                        const tb = Render.createThinkingBlock(block.thinking || '');
                        toolsContainer.appendChild(tb);
                    }
                }
            } else if (typeof content === 'string') {
                textContainer.innerHTML = this._renderMarkdown(content);
            }
        }
    },

    _appendUserMessage(text) {
        const messagesEl = document.getElementById('messages');
        const div = document.createElement('div');
        div.className = 'message user';
        div.innerHTML = `
            <div class="message-role">You</div>
            <div class="message-body">${this._renderMarkdown(text)}</div>
        `;
        messagesEl.appendChild(div);
    },

    _startAssistantMessage() {
        const messagesEl = document.getElementById('messages');
        const div = document.createElement('div');
        div.className = 'message assistant';
        div.innerHTML = `
            <div class="message-role">Genie</div>
            <div class="assistant-tools"></div>
            <div class="assistant-text message-body"></div>
        `;
        messagesEl.appendChild(div);
        return div;
    },

    /** Extract text from message content (could be string or array). */
    _extractText(content) {
        if (typeof content === 'string') return content;
        if (Array.isArray(content)) {
            return content
                .filter(b => b.type === 'text')
                .map(b => b.text)
                .join('\n');
        }
        return '';
    },

    /** Render markdown to HTML using marked.js. */
    _renderMarkdown(text) {
        if (typeof marked !== 'undefined') {
            return marked.parse(text || '', { breaks: true });
        }
        // Fallback: basic escaping + line breaks
        const div = document.createElement('div');
        div.textContent = text || '';
        return div.innerHTML.replace(/\n/g, '<br>');
    },

    _scrollToBottom() {
        const el = document.getElementById('messages');
        requestAnimationFrame(() => {
            el.scrollTop = el.scrollHeight;
        });
    },

    _toggleInput(enabled) {
        document.getElementById('user-input').disabled = !enabled;
        this._updateSendBtn();
    },

    _updateSendBtn() {
        const btn = document.getElementById('send-btn');
        const input = document.getElementById('user-input');
        btn.disabled = !this.chatId || this.isSending || !input.value.trim();
    },
};
