// Event wiring and startup.

function handleAction() {
    if (isGenerating) {
        stopAI();
    } else if (isAiErrorState && !inputEl.value.trim()) {
        retryLastAiRequest();
    } else {
        sendMessage();
    }
}

function setupEventListeners() {
    // Action button
    actionBtn.addEventListener('mousedown', function (ev) {
        if (isGenerating) {
            ev.stopPropagation();
            stopAI();
        }
    }, true);

    actionBtn.addEventListener('click', function (e) {
        e.preventDefault();
        handleAction();
    });

    // Input handling
    inputEl.addEventListener('input', function () {
        if (isAiErrorState && this.value.trim()) {
            setAiErrorState(false);
        }
        this.scrollTop = 0;
        resizeChatInput();
    });

    inputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleAction();
        }
    });

    // Attach menu
    attachBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        attachMenu.classList.toggle('open');
    });

    window.addEventListener('click', function () {
        attachMenu.classList.remove('open');
    });

    // Chat tabs — add button
    document.getElementById('chat-tab-add').addEventListener('click', function () {
        createChatTab();
    });

    // Pending actions
    document.getElementById('pending-apply-btn').addEventListener('click', applyPendingChanges);
    document.getElementById('pending-reject-btn').addEventListener('click', rejectPendingCodePreview);

    // Attach menu items
    document.getElementById('attach-item-image').addEventListener('click', function () { requestAttachment('image'); });
    document.getElementById('attach-item-doc').addEventListener('click', function () { requestAttachment('document'); });
    document.getElementById('attach-item-chart').addEventListener('click', function () { requestAttachment('chart'); });

    // Resizer — collapsible editor/chat panels (like Chart tab QSplitter)
    let isResizing = false;

    resizer.addEventListener('mousedown', () => {
        isResizing = true;
        resizer.classList.add('active');
        document.body.style.cursor = 'col-resize';
        // Panel stays collapsed on mousedown — only restores on mousemove past threshold
    });

    window.addEventListener('mousemove', (e) => {
        if (!isResizing) return;
        const total = window.innerWidth;
        const edW = e.clientX;
        const chatW = total - edW;

        if (_panelCollapsed === 'chat') {
            // Chat is hidden; restore only when dragged far enough (intended chat > CHAT_MIN + SNAP_THRESHOLD)
            if (chatW > CHAT_MIN + SNAP_THRESHOLD) {
                _panelCollapsed = null;
                chatContainer.style.display = 'flex';
                leftPanel.style.width = Math.max(EDITOR_MIN, total - chatW) + 'px';
            }
        } else if (_panelCollapsed === 'editor') {
            // Editor is hidden; restore only when dragged far enough
            if (edW > EDITOR_MIN + SNAP_THRESHOLD) {
                _panelCollapsed = null;
                leftPanel.style.display = 'flex';
                chatContainer.style.width = ''; // reset to flex sizing
                leftPanel.style.width = Math.max(EDITOR_MIN, Math.min(total - CHAT_MIN, edW)) + 'px';
            }
        } else {
            // Normal resize — stick at minimum boundary before collapsing
            if (chatW < CHAT_MIN) {
                // Keep chat at minimum width, don't collapse immediately
                let overscroll = CHAT_MIN - chatW;
                if (overscroll > SNAP_THRESHOLD) {
                    // Dragged far enough past minimum → collapse
                    _panelCollapsed = 'chat';
                    chatContainer.style.display = 'none';
                    leftPanel.style.width = '100%';
                } else {
                    // Stick at minimum width (like hitting a wall)
                    leftPanel.style.width = (total - CHAT_MIN) + 'px';
                }
            } else if (edW < EDITOR_MIN) {
                // Keep editor at minimum width, don't collapse immediately
                let overscroll = EDITOR_MIN - edW;
                if (overscroll > SNAP_THRESHOLD) {
                    // Dragged far enough past minimum → collapse
                    _panelCollapsed = 'editor';
                    leftPanel.style.display = 'none';
                    chatContainer.style.width = '100%';
                } else {
                    // Stick at minimum width
                    leftPanel.style.width = EDITOR_MIN + 'px';
                }
            } else {
                // Normal resize within bounds
                leftPanel.style.width = Math.max(EDITOR_MIN, Math.min(total - CHAT_MIN, edW)) + 'px';
            }
        }
        if (editor) editor.layout();
    });

    window.addEventListener('mouseup', () => {
        if (isResizing) {
            isResizing = false;
            resizer.classList.remove('active');
            document.body.style.cursor = 'default';
            if (editor) editor.layout();
        }
    });

    // Global error handling
    window.addEventListener('error', function (e) {
        console.error('JS Error:', e.message, e.filename, e.lineno, e.error);
    });
}

// ============= MARKED CONFIGURATION =============

function showEditorLoadError(message) {
    monacoEditEl.innerHTML =
        '<div style="color:var(--red);padding:20px;font:13px Segoe UI, sans-serif;line-height:1.45;">'
        + escapeHtml(message)
        + '</div>';
}

function bootstrap() {
    configureMarked();

    // Initialize chat tabs (loads from localStorage or creates default)
    initChatTabs();

    if (typeof require !== 'function') {
        showEditorLoadError('Monaco loader did not load. Check CDN/network access or Qt WebEngine remote-script permissions.');
        return;
    }

    require(['vs/editor/editor.main'], function () {
        const editorInitialized = initEditor();

        if (editorInitialized) {
            setupEventListeners();
            initBridge();
        } else {
            showEditorLoadError('Failed to initialize Monaco editor. Please restart the application.');
        }
    }, function (err) {
        console.error('Failed to load Monaco editor:', err);
        showEditorLoadError('Failed to load Monaco editor from CDN. Check network access.');
    });
}

// Start when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrap);
} else {
    bootstrap();
}
