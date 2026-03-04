/**
 * app.js — Entry point. Wires all modules together.
 */

document.addEventListener('DOMContentLoaded', async () => {
    // ── Initialize modules ────────────────────────────────────────────

    Files.init();

    // Sidebar: when a chat is selected, load it
    await Sidebar.init(async (chatId) => {
        await Chat.load(chatId);
    });

    // ── Input handling ────────────────────────────────────────────────

    const input = document.getElementById('user-input');
    const sendBtn = document.getElementById('send-btn');

    // Auto-resize textarea
    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 180) + 'px';
        Chat._updateSendBtn();
    });

    // Send on Enter (Shift+Enter for newline)
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            Chat.send();
        }
    });

    // Send button
    sendBtn.addEventListener('click', () => Chat.send());

    // ── Load initial state ────────────────────────────────────────────

    // If there are chats, load the first one
    const chats = await API.listChats();
    if (chats.length > 0) {
        Sidebar.switchTo(chats[0].id);
    } else {
        // No chats — show empty state
        Chat.load(null);
    }

    // ── Health check (non-blocking) ───────────────────────────────────

    try {
        const h = await API.health();
        const indicator = document.getElementById('status-indicator');
        const parts = [];
        if (h.trino === 'ok') parts.push('Trino ✓');
        else if (h.trino === 'unreachable') parts.push('Trino ✗');
        if (parts.length) indicator.textContent = parts.join(' · ');
    } catch (e) {
        // Ignore — non-critical
    }
});
