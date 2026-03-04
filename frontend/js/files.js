/**
 * files.js — File upload and attachment management.
 *
 * Files are uploaded immediately to the server. Their IDs are stored in
 * Chat.fileIds and sent along with the next message.
 */

const Files = {
    init() {
        // Create a hidden file input
        this._input = document.createElement('input');
        this._input.type = 'file';
        this._input.multiple = true;
        this._input.style.display = 'none';
        this._input.addEventListener('change', () => this._handleFiles(this._input.files));
        document.body.appendChild(this._input);

        // Drag & drop on the messages area
        const main = document.getElementById('main');
        main.addEventListener('dragover', (e) => {
            e.preventDefault();
            main.style.outline = '2px dashed var(--accent)';
            main.style.outlineOffset = '-4px';
        });
        main.addEventListener('dragleave', () => {
            main.style.outline = '';
        });
        main.addEventListener('drop', (e) => {
            e.preventDefault();
            main.style.outline = '';
            if (e.dataTransfer.files.length) {
                this._handleFiles(e.dataTransfer.files);
            }
        });
    },

    /** Open the file picker dialog. */
    openPicker() {
        this._input.value = '';
        this._input.click();
    },

    /** Clear all file pills and reset Chat.fileIds. */
    clear() {
        Chat.fileIds = [];
        document.getElementById('file-pills').innerHTML = '';
    },

    /** Remove a specific file by ID. */
    remove(fileId) {
        Chat.fileIds = Chat.fileIds.filter(id => id !== fileId);
        const pill = document.querySelector(`.file-pill[data-id="${fileId}"]`);
        if (pill) pill.remove();
    },

    // ── Private ──────────────────────────────────────────────────────

    async _handleFiles(fileList) {
        if (!Chat.chatId) return;

        for (const file of fileList) {
            try {
                const result = await API.uploadFile(Chat.chatId, file);
                Chat.fileIds.push(result.id);
                this._addPill(result.id, result.filename);
            } catch (err) {
                console.error('Upload failed:', err);
            }
        }
    },

    _addPill(fileId, filename) {
        const pills = document.getElementById('file-pills');
        const pill = document.createElement('span');
        pill.className = 'file-pill';
        pill.dataset.id = fileId;

        const icon = this._fileIcon(filename);
        pill.innerHTML = `
            ${icon} ${this._esc(filename)}
            <button class="file-pill-remove" onclick="Files.remove('${fileId}')">&times;</button>
        `;
        pills.appendChild(pill);
    },

    _fileIcon(name) {
        const ext = (name.split('.').pop() || '').toLowerCase();
        const map = {
            pdf: '📄', xlsx: '📊', xls: '📊', csv: '📊', tsv: '📊',
            png: '🖼️', jpg: '🖼️', jpeg: '🖼️', webp: '🖼️',
            py: '🐍', js: '📜', json: '📜', md: '📝', txt: '📝',
        };
        return map[ext] || '📎';
    },

    _esc(str) {
        const d = document.createElement('div');
        d.textContent = str;
        return d.innerHTML;
    },
};
