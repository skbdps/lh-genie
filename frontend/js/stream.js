/**
 * stream.js — SSE client for the POST /api/chats/{id}/messages endpoint.
 *
 * Since EventSource only supports GET, we use fetch() with a ReadableStream
 * and manually parse the SSE protocol (event: / data: lines).
 */

const Stream = {
    /**
     * Send a message and process the SSE response stream.
     *
     * @param {string}   chatId   - Chat ID
     * @param {string}   content  - User message text
     * @param {string[]} fileIds  - File IDs to attach
     * @param {Object}   handlers - Event handlers keyed by event type
     *   { status, tool_start, tool_result, html_output, text, thinking, error, done }
     * @returns {Promise<void>} Resolves when stream ends
     */
    async send(chatId, content, fileIds, handlers) {
        const res = await fetch(`/api/chats/${chatId}/messages`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content, file_ids: fileIds }),
        });

        if (!res.ok) {
            const err = await res.text();
            if (handlers.error) handlers.error({ message: `HTTP ${res.status}: ${err}` });
            return;
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();

        let buffer = '';
        let currentEvent = 'message';
        let dataLines = [];

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');

            // Keep the last incomplete line in buffer
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('event:')) {
                    currentEvent = line.slice(6).trim();
                } else if (line.startsWith('data:')) {
                    // SSE spec: multi-line data is concatenated with newlines
                    dataLines.push(line.slice(5).trim());
                } else if (line === '' || line === '\r') {
                    // Empty line = end of SSE message, dispatch it
                    if (dataLines.length > 0) {
                        const fullData = dataLines.join('\n');
                        try {
                            const data = JSON.parse(fullData);
                            const handler = handlers[currentEvent];
                            if (handler) handler(data);
                        } catch (e) {
                            console.warn('Failed to parse SSE data:', fullData.slice(0, 200), e);
                        }
                    }
                    currentEvent = 'message';
                    dataLines = [];
                }
            }
        }

        // Process any remaining data in buffer
        if (dataLines.length > 0) {
            const fullData = dataLines.join('\n');
            try {
                const data = JSON.parse(fullData);
                const handler = handlers[currentEvent];
                if (handler) handler(data);
            } catch (e) {
                // ignore
            }
        }
    },
};
