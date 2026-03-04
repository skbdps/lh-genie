/**
 * api.js — HTTP client for all REST endpoints.
 *
 * Every function returns a parsed JSON response or throws.
 * The SSE streaming endpoint is handled separately in stream.js.
 */

const API = {
    /** @returns {Promise<Array<{id, title, created_at, updated_at}>>} */
    async listChats() {
        const res = await fetch('/api/chats');
        if (!res.ok) throw new Error(`Failed to list chats: ${res.status}`);
        return res.json();
    },

    /** @returns {Promise<{id, title, created_at}>} */
    async createChat(title = 'New Chat') {
        const res = await fetch('/api/chats', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title }),
        });
        if (!res.ok) throw new Error(`Failed to create chat: ${res.status}`);
        return res.json();
    },

    /** @returns {Promise<{id, title, messages, files}>} */
    async getChat(chatId) {
        const res = await fetch(`/api/chats/${chatId}`);
        if (!res.ok) throw new Error(`Failed to get chat: ${res.status}`);
        return res.json();
    },

    async renameChat(chatId, title) {
        const res = await fetch(`/api/chats/${chatId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title }),
        });
        if (!res.ok) throw new Error(`Failed to rename chat: ${res.status}`);
        return res.json();
    },

    async deleteChat(chatId) {
        const res = await fetch(`/api/chats/${chatId}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(`Failed to delete chat: ${res.status}`);
        return res.json();
    },

    /** Upload a file to a chat. Returns file metadata. */
    async uploadFile(chatId, file) {
        const form = new FormData();
        form.append('file', file);
        const res = await fetch(`/api/chats/${chatId}/files`, {
            method: 'POST',
            body: form,
        });
        if (!res.ok) throw new Error(`Failed to upload file: ${res.status}`);
        return res.json();
    },

    async listFiles(chatId) {
        const res = await fetch(`/api/chats/${chatId}/files`);
        if (!res.ok) throw new Error(`Failed to list files: ${res.status}`);
        return res.json();
    },

    async deleteFile(fileId) {
        const res = await fetch(`/api/files/${fileId}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(`Failed to delete file: ${res.status}`);
        return res.json();
    },

    async health() {
        const res = await fetch('/api/health');
        if (!res.ok) throw new Error(`Health check failed: ${res.status}`);
        return res.json();
    },

    /** Build the URL for downloading a sandbox output file. */
    outputUrl(chatId, filename) {
        return `/api/chats/${chatId}/outputs/${encodeURIComponent(filename)}`;
    },
};
