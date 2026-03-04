/**
 * sidebar.js — Chat list management.
 */

const Sidebar = {
    currentChatId: null,
    onChatSwitch: null,   // callback(chatId)

    async init(onChatSwitch) {
        this.onChatSwitch = onChatSwitch;
        document.getElementById('new-chat-btn').addEventListener('click', () => this.createChat());
        await this.refresh();
    },

    /** Reload chat list from server and re-render. */
    async refresh() {
        const chats = await API.listChats();
        this._render(chats);
    },

    /** Create a new chat and switch to it. */
    async createChat() {
        const chat = await API.createChat();
        this.currentChatId = chat.id;
        await this.refresh();
        if (this.onChatSwitch) this.onChatSwitch(chat.id);
    },

    /** Switch to an existing chat. */
    switchTo(chatId) {
        this.currentChatId = chatId;
        // Update active state in sidebar
        document.querySelectorAll('.chat-item').forEach(el => {
            el.classList.toggle('active', el.dataset.chatId === chatId);
        });
        if (this.onChatSwitch) this.onChatSwitch(chatId);
    },

    /** Delete a chat. */
    async deleteChat(chatId, e) {
        e.stopPropagation();
        if (!confirm('Delete this chat?')) return;

        await API.deleteChat(chatId);

        // If we deleted the active chat, switch to another
        if (chatId === this.currentChatId) {
            this.currentChatId = null;
        }
        await this.refresh();

        // Switch to first chat if any
        const first = document.querySelector('.chat-item');
        if (first && !this.currentChatId) {
            this.switchTo(first.dataset.chatId);
        } else if (!first) {
            // No chats left — show empty state
            if (this.onChatSwitch) this.onChatSwitch(null);
        }
    },

    // ── Private ──────────────────────────────────────────────────────

    _render(chats) {
        const list = document.getElementById('chat-list');
        list.innerHTML = '';

        for (const chat of chats) {
            const div = document.createElement('div');
            div.className = 'chat-item' + (chat.id === this.currentChatId ? ' active' : '');
            div.dataset.chatId = chat.id;
            div.onclick = () => this.switchTo(chat.id);

            const title = document.createElement('span');
            title.className = 'chat-item-title';
            title.textContent = chat.title || 'New Chat';

            const del = document.createElement('button');
            del.className = 'chat-item-delete';
            del.textContent = '×';
            del.onclick = (e) => this.deleteChat(chat.id, e);

            div.appendChild(title);
            div.appendChild(del);
            list.appendChild(div);
        }
    },
};
