// Chat tabs, messages, AI streaming, attachments, and markdown rendering.

function safeLocalStorageGet(key) {
    if (!_localStorageAvailable) return null;
    try { return localStorage.getItem(key); } catch (e) { return null; }
}

function safeLocalStorageSet(key, value) {
    if (!_localStorageAvailable) return;
    try { localStorage.setItem(key, value); } catch (e) { }
}

function pushStateToPython() {
    if (_suppressChatTabsSync) return;
    try {
        const state = JSON.stringify({
            chatTabs: chatTabs,
            activeChatTabId: activeChatTabId,
            tabIdCounter: tabIdCounter
        });
        // Local storage as fallback
        safeLocalStorageSet(CHAT_PERSIST_KEY, JSON.stringify(chatTabs));
        // Send to Python for cross-panel sync
        if (pyBridge && typeof pyBridge.receive_chat_tabs_state === 'function') {
            pyBridge.receive_chat_tabs_state(state);
        }
    } catch (e) {
        console.warn('Failed to push chat tabs state:', e);
    }
}

function persistChatTabs() {
    pushStateToPython();
}

function loadChatTabsFromStorage() {
    const raw = safeLocalStorageGet(CHAT_PERSIST_KEY);
    if (raw) {
        try {
            const parsed = JSON.parse(raw);
            if (Array.isArray(parsed) && parsed.length > 0) {
                chatTabs = parsed;
                let maxId = 0;
                chatTabs.forEach(t => { if (t.id > maxId) maxId = t.id; });
                tabIdCounter = Math.max(tabIdCounter, maxId + 1);
                return true;
            }
        } catch (e) {
            console.warn('Failed to parse chat tabs:', e);
        }
    }
    return false;
}

function initChatTabs() {
    const loaded = loadChatTabsFromStorage();
    if (!loaded) {
        const defaultTab = {
            id: 1,
            label: 'Chat 1',
            messages: []
        };
        chatTabs = [defaultTab];
        tabIdCounter = 2;
        persistChatTabs();
    }
    activeChatTabId = chatTabs[0].id;
    renderChatTabs();
    renderActiveTabMessages();
}

// ============= CROSS-PANEL SYNC (called from Python) =============
window.applyChatTabsState = function (stateJson) {
    try {
        const state = JSON.parse(stateJson);
        if (!state || !state.chatTabs) return;

        _suppressChatTabsSync = true;

        chatTabs = state.chatTabs;
        activeChatTabId = state.activeChatTabId;
        if (state.tabIdCounter) {
            tabIdCounter = Math.max(tabIdCounter, state.tabIdCounter);
        }

        // Ensure active tab exists
        if (!chatTabs.find(t => t.id === activeChatTabId)) {
            activeChatTabId = chatTabs.length > 0 ? chatTabs[0].id : null;
        }

        safeLocalStorageSet(CHAT_PERSIST_KEY, JSON.stringify(chatTabs));

        renderChatTabs();
        renderActiveTabMessages();

        // Release suppression after a microtask to allow next local change
        setTimeout(function () { _suppressChatTabsSync = false; }, 0);
    } catch (e) {
        _suppressChatTabsSync = false;
        console.warn('Failed to apply chat tabs state:', e);
    }
};

if (window.__aiEditorPreludeQueue && window.__aiEditorPreludeQueue.chatTabsStateJson) {
    const queuedChatTabsState = window.__aiEditorPreludeQueue.chatTabsStateJson;
    window.__aiEditorPreludeQueue.chatTabsStateJson = null;
    window.applyChatTabsState(queuedChatTabsState);
}

// ============= CHAT TABS — UI RENDERING =============
function renderChatTabs() {
    const tabsContainer = document.getElementById('chat-tabs');
    if (!tabsContainer) return;

    // Clear existing tab elements (keep the add button)
    const addBtn = document.getElementById('chat-tab-add');
    tabsContainer.innerHTML = '';
    if (addBtn) tabsContainer.appendChild(addBtn);

    chatTabs.forEach(tab => {
        const tabEl = document.createElement('div');
        tabEl.className = 'chat-tab' + (tab.id === activeChatTabId ? ' active' : '');
        tabEl.setAttribute('data-tab-id', tab.id);
        tabEl.title = tab.label;

        const labelSpan = document.createElement('span');
        labelSpan.className = 'tab-label';
        labelSpan.textContent = tab.label;
        tabEl.appendChild(labelSpan);

        const closeBtn = document.createElement('button');
        closeBtn.className = 'tab-close';
        closeBtn.innerHTML = '&#x2715;';
        closeBtn.title = 'Close chat';
        closeBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            deleteChatTab(tab.id);
        });
        tabEl.appendChild(closeBtn);

        tabEl.addEventListener('click', function (e) {
            if (e.target === closeBtn) return;
            switchChatTab(tab.id);
        });

        // Double click to rename
        tabEl.addEventListener('dblclick', function (e) {
            if (e.target === closeBtn) return;
            renameChatTab(tab.id);
        });

        tabsContainer.insertBefore(tabEl, addBtn);
    });

    // Scroll active tab into view
    requestAnimationFrame(() => {
        const activeEl = tabsContainer.querySelector('.chat-tab.active');
        if (activeEl) {
            activeEl.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
        }
    });
}

function renderActiveTabMessages() {
    chatHistory.innerHTML = '';
    const activeTab = getActiveTab();
    if (!activeTab) return;

    activeTab.messages.forEach(msg => {
        if (msg.role === 'user') {
            const escaped = (msg.content || '').replace(/\n/g, '<br>');
            chatHistory.innerHTML += `<div class="message user-message">${escaped}</div>`;
        } else if (msg.role === 'assistant') {
            let html;
            try {
                html = marked.parse(msg.content);
            } catch (e) {
                html = escapeHtml(msg.content);
            }
            chatHistory.innerHTML += `<div class="message ai-message">${html}</div>`;
        } else if (msg.role === 'system') {
            const color = msg.isError ? 'var(--red)' : '#4CAF50';
            chatHistory.innerHTML += `<div class="message" style="background-color: transparent; border: 1px solid ${color}; color: ${color}; align-self: center; font-size: 11px; padding: 4px 10px; opacity: 0.8;">${escapeHtml(msg.content)}</div>`;
        }
    });
    scrollChatToBottom();
}

// ============= CHAT TABS — OPERATIONS =============
function getActiveTab() {
    return chatTabs.find(t => t.id === activeChatTabId) || null;
}

function ensureActiveTabExists() {
    if (!getActiveTab()) {
        if (chatTabs.length > 0) {
            activeChatTabId = chatTabs[0].id;
        } else {
            createChatTab();
        }
        renderChatTabs();
        renderActiveTabMessages();
    }
}

function createChatTab(label) {
    tabIdCounter += 1;
    const id = tabIdCounter;
    // Generate sequential label without gaps: Chat 1, Chat 2, ...
    const seqNum = chatTabs.length + 1;
    const newTab = {
        id: id,
        label: label || ('Chat ' + seqNum),
        messages: []
    };
    chatTabs.push(newTab);
    activeChatTabId = id;
    renderChatTabs();
    renderActiveTabMessages();
    persistChatTabs();
    return id;
}

function switchChatTab(tabId) {
    if (activeChatTabId === tabId) return;
    activeChatTabId = tabId;
    renderChatTabs();
    renderActiveTabMessages();
}

function deleteChatTab(tabId) {
    if (chatTabs.length <= 1) {
        // Don't delete the last tab — clear it instead
        const tab = chatTabs[0];
        tab.messages = [];
        tab.label = 'Chat 1';
        activeChatTabId = tab.id;
        renderChatTabs();
        renderActiveTabMessages();
        persistChatTabs();
        return;
    }

    const idx = chatTabs.findIndex(t => t.id === tabId);
    if (idx === -1) return;

    chatTabs.splice(idx, 1);

    if (activeChatTabId === tabId) {
        // Switch to the nearest tab
        const newIdx = Math.min(idx, chatTabs.length - 1);
        activeChatTabId = chatTabs[newIdx].id;
    }

    renderChatTabs();
    renderActiveTabMessages();
    persistChatTabs();
}

function renameChatTab(tabId) {
    const tab = chatTabs.find(t => t.id === tabId);
    if (!tab) return;

    const newLabel = prompt('Rename chat:', tab.label);
    if (newLabel !== null && newLabel.trim()) {
        tab.label = newLabel.trim();
        renderChatTabs();
        persistChatTabs();
    }
}

function addMessageToActiveTab(role, content, extra) {
    ensureActiveTabExists();
    const tab = getActiveTab();
    if (!tab) return;

    const msg = { role: role, content: content };
    if (extra) {
        Object.keys(extra).forEach(k => { msg[k] = extra[k]; });
    }
    tab.messages.push(msg);
    persistChatTabs();
}

// ============= UTILITY FUNCTIONS =============

function buildGeneratingPlaceholderHtml(phaseText) {
    const p = phaseText || 'Sending…';
    return `<div class="ai-generating-root">
<div id="ai-phase-line" class="thinking">${escapeHtml(p)}</div>
<div class="ai-phase-hint">No change = usually API/network wait. Red square = stop.</div>
            </div>`;
}

function setAiPhaseMessage(text) {
    aiEditorDebug('setAiPhaseMessage', {
        len: text != null ? String(text).length : 0,
        preview: String(text != null ? text : '').slice(0, 120)
    });

    const line = document.getElementById('ai-phase-line');
    if (line) {
        line.textContent = text != null ? String(text) : '';
        return;
    }

    if (currentAiMessageDiv) {
        const fb = currentAiMessageDiv.querySelector('.thinking');
        if (fb) fb.textContent = text != null ? String(text) : '';
    }
}
function cancelStreamMarkdownFlush() {
    if (streamFlushRaf) {
        cancelAnimationFrame(streamFlushRaf);
        streamFlushRaf = 0;
    }
}

function applyStreamMarkdownFlush() {
    if (!currentAiMessageDiv) {
        aiEditorDebug('applyStreamMarkdownFlush_skip', { reason: 'no_current_div' });
        return;
    }

    if (currentAiMarkdown === '') {
        aiEditorDebug('applyStreamMarkdownFlush_skip', { reason: 'empty_markdown' });
        return;
    }

    try {
        currentAiMessageDiv.innerHTML = marked.parse(currentAiMarkdown);
    } catch (e) {
        console.error('Markdown parse error:', e);
        currentAiMessageDiv.textContent = currentAiMarkdown;
    }

    const patchConsumed = tryConsumeLatestPatch(currentAiMarkdown);

    if (patchConsumed) {
        pendingCodeCandidate = '';
        applyStreamedCodePreview(pendingPatchPreviewResult);
        updatePendingActionsBar();
    } else {
        const latestCode = extractLatestCodeBlock(currentAiMarkdown);
        if (latestCode) {
            pendingPatchChunks = null;
            pendingPatchPreviewResult = null;
            pendingCodeCandidate = latestCode;
            applyStreamedCodePreview(latestCode);
            updatePendingActionsBar();
        }
    }

    markdownFlushCount += 1;

    if (markdownFlushCount <= 8 || markdownFlushCount % 25 === 0) {
        aiEditorDebug('applyStreamMarkdownFlush', {
            flushN: markdownFlushCount,
            markdownLen: currentAiMarkdown.length,
            patchConsumed,
            pendingCodeLen: pendingCodeCandidate ? pendingCodeCandidate.length : 0
        });
    }

    scrollChatToBottom();
}

function scheduleStreamMarkdownFlush() {
    if (streamFlushRaf) return;

    const now = Date.now();
    if (now - lastFlushTime < FLUSH_THROTTLE_MS) {
        // Throttle flushes in QtWebEngine to prevent UI freezes
        if (!streamFlushRaf) {
            streamFlushRaf = requestAnimationFrame(() => {
                streamFlushRaf = 0;
                lastFlushTime = Date.now();
                applyStreamMarkdownFlush();
            });
        }
        return;
    }

    streamFlushRaf = requestAnimationFrame(() => {
        streamFlushRaf = 0;
        lastFlushTime = Date.now();
        applyStreamMarkdownFlush();
    });
}

function scrollChatToBottom() {
    if (chatHistory) {
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }
}

function resizeChatInput() {
    inputEl.style.height = '32px';
    inputEl.style.height = inputEl.scrollHeight + 'px';
    inputEl.style.overflowY = inputEl.scrollHeight > 150 ? 'auto' : 'hidden';
}

function clearChatInput() {
    inputEl.value = '';
    inputEl.scrollTop = 0;
    inputEl.style.height = '32px';
    inputEl.style.overflowY = 'hidden';
}

function appendSystemMessage(msg, isError) {
    addMessageToActiveTab('system', msg, { isError: !!isError });
    const color = isError ? 'var(--red)' : '#4CAF50';
    const messageHTML = `<div class="message" style="background-color: transparent; border: 1px solid ${color}; color: ${color}; align-self: center; font-size: 11px; padding: 4px 10px; opacity: 0.8;">${escapeHtml(msg)}</div>`;
    chatHistory.innerHTML += messageHTML;
    scrollChatToBottom();
}
window.appendSystemMessage = appendSystemMessage;

function setGeneratingState(generating) {
    isGenerating = generating;

    if (generating) {
        isAiErrorState = false;
        actionBtn.classList.remove('error-mode');
        actionBtn.classList.add('stop-mode');
        iconSend.style.display = 'none';
        iconStop.style.display = 'block';
        iconReload.style.display = 'none';
    } else {
        actionBtn.classList.remove('stop-mode');
        iconStop.style.display = 'none';
        if (isAiErrorState) {
            actionBtn.classList.add('error-mode');
            actionBtn.title = 'Retry last AI request';
            actionBtn.setAttribute('aria-label', 'Retry last AI request');
            iconSend.style.display = 'none';
            iconReload.style.display = 'block';
        } else {
            actionBtn.classList.remove('error-mode');
            actionBtn.title = 'Send / Stop AI';
            actionBtn.setAttribute('aria-label', 'Send message');
            iconSend.style.display = 'block';
            iconReload.style.display = 'none';
        }
    }
}

function setAiErrorState(enabled) {
    isAiErrorState = !!enabled && !!lastAiRequest;
    if (!isGenerating) {
        setGeneratingState(false);
    }
}

// ============= BRIDGE COMMUNICATION =============
function safeBridgeCall(method, ...args) {
    if (!pyBridge || typeof pyBridge[method] !== 'function') {
        console.error(`Bridge method "${method}" not available`);
        return false;
    }

    try {
        pyBridge[method](...args);
        return true;
    } catch (e) {
        console.error(`Bridge call "${method}" failed:`, e);
        return false;
    }
}

function dispatchAiRequest(request) {
    if (!pyBridge) {
        appendSystemMessage('Connection not ready. Please wait...', true);
        return;
    }

    if (!request || !request.text) return;

    previewBaselineCode = getEditorValue();
    pendingCodeCandidate = '';
    pendingPatchChunks = null;
    pendingPatchPreviewResult = null;
    clearPreviewViewZones();

    if (isEditorReady()) {
        previewDecorations = editor.deltaDecorations(previewDecorations, []);
    }

    updatePendingActionsBar();

    lastAiRequest = {
        text: request.text,
        code: request.code,
        selectionPayload: request.selectionPayload || ''
    };

    clearChatInput();
    setGeneratingState(true);

    addMessageToActiveTab('user', request.text);
    chatHistory.innerHTML += `<div class="message user-message">${escapeHtml(request.text).replace(/\n/g, '<br>')}</div>`;

    currentAiMessageDiv = document.createElement('div');
    currentAiMessageDiv.className = 'message ai-message';
    currentAiMessageDiv.innerHTML = buildGeneratingPlaceholderHtml('Sending…');
    chatHistory.appendChild(currentAiMessageDiv);

    currentAiMarkdown = '';
    scrollChatToBottom();

    aiStreamChunkNum = 0;
    markdownFlushCount = 0;
    lastFlushTime = 0;

    aiEditorDebug('sendMessage', {
        textLen: request.text.length,
        codeLen: request.code.length,
        selectionPayloadLen: request.selectionPayload.length
    });

    safeBridgeCall('receive_chat_request_ext', request.text, request.code, request.selectionPayload);

    selectedAttachments = [];
    renderAttachments();
}

function buildAiRequestFromInput() {
    const text = inputEl.value.trim();
    if (!text) return null;

    const code = getEditorValue();
    const payloadObj = chartSelectionContext ? JSON.parse(JSON.stringify(chartSelectionContext)) : {};

    if (selectedAttachments.length > 0) {
        payloadObj.attachments = selectedAttachments;
    }

    // Include chat history from the active tab (last 20 messages)
    const activeTab = getActiveTab();
    if (activeTab && activeTab.messages.length > 0) {
        payloadObj.chatHistory = activeTab.messages.slice(-20);
    }

    return {
        text: text,
        code: code,
        selectionPayload: Object.keys(payloadObj).length > 0 ? JSON.stringify(payloadObj) : ''
    };
}

function sendMessage() {
    const request = buildAiRequestFromInput();
    if (!request) return;
    dispatchAiRequest(request);
}

function retryLastAiRequest() {
    if (!lastAiRequest) return;
    dispatchAiRequest(lastAiRequest);
}

function stopAI() {
    aiEditorDebug('stopAI', {
        streamChunks: aiStreamChunkNum,
        markdownLen: currentAiMarkdown.length
    });
    // Immediately replace the placeholder to stop animation
    if (currentAiMessageDiv && !currentAiMarkdown) {
        currentAiMessageDiv.innerHTML =
            '<div style="font-size:11px;color:var(--text3);font-style:normal;padding:4px 0;">'
            + '&#10074;&#10074; Stopping…'
            + '</div>';
    }
    safeBridgeCall('receive_stop_ai');
}

// ============= ATTACHMENT FUNCTIONS =============

function requestAttachment(kind) {
    attachMenu.classList.remove('open');
    if (!pyBridge) return;

    if (kind === 'chart') {
        requestChartSelection();
        return;
    }

    safeBridgeCall('receive_attachment_request', kind);
}

function requestChartSelection() {
    if (safeBridgeCall('receive_chart_selection_request')) {
        appendSystemMessage('Selection mode enabled. Drag on chart to capture area context.');
    }
}

function renderAttachments() {
    const host = document.getElementById('attachments-list');
    if (!host) return;

    if (!selectedAttachments.length) {
        host.innerHTML = '';
        return;
    }

    host.innerHTML = selectedAttachments.map((a, idx) => {
        const label = `${a.kind === 'image' ? '🖼' : '📄'} ${escapeHtml(a.name)}`;
        return `<span class="attachment-chip">${label}<button onclick="window._removeAttachment(${idx})" aria-label="Remove attachment">✕</button></span>`;
    }).join('');
}

function removeAttachment(idx) {
    selectedAttachments.splice(idx, 1);
    renderAttachments();
}

// Expose for inline onclick
window._removeAttachment = removeAttachment;

function set_attachment_from_python(payload) {
    if (!payload || !payload.path) return;
    selectedAttachments.push(payload);
    renderAttachments();
    appendSystemMessage(`Attached: ${escapeHtml(payload.name)}`);
}

// ============= PUBLIC API (called from Python) =============

// (4.6) Enable/disable vision-dependent UI
window.setVisionCapability = function(enabled) {
    canUseVision = !!enabled;
    if (attachBtn) {
        if (canUseVision) {
            attachBtn.disabled = false;
            attachBtn.title = 'Attach image or document';
            document.getElementById('attach-item-image').style.display = 'block';
        } else {
            attachBtn.disabled = false; // still allow docs, just block image
            document.getElementById('attach-item-image').style.display = 'none';
            // Remove any image attachments already selected
            selectedAttachments = selectedAttachments.filter(a => a.kind !== 'image');
            renderAttachments();
        }
    }
};

window.setAiPhaseMessage = setAiPhaseMessage;

if (window.__aiEditorPreludeQueue && window.__aiEditorPreludeQueue.aiPhaseText !== null) {
    const queuedAiPhaseText = window.__aiEditorPreludeQueue.aiPhaseText;
    window.__aiEditorPreludeQueue.aiPhaseText = null;
    window.setAiPhaseMessage(queuedAiPhaseText);
}

window.stream_ai_chunk = function (chunk) {
    if (!currentAiMessageDiv) {
        aiEditorDebug('stream_ai_chunk_skip', {
            reason: 'no_current_div',
            chunkLen: (chunk || '').length
        });
        return;
    }

    aiStreamChunkNum += 1;
    currentAiMarkdown += chunk;

    if (aiStreamChunkNum <= 10 || aiStreamChunkNum % 40 === 0) {
        aiEditorDebug('stream_ai_chunk', {
            n: aiStreamChunkNum,
            chunkLen: (chunk || '').length,
            cumulativeLen: currentAiMarkdown.length
        });
    }

    scheduleStreamMarkdownFlush();
};

window.finish_ai_message = function (finalText) {
    aiEditorDebug('finish_ai_message_begin', {
        streamChunks: aiStreamChunkNum,
        markdownLen: currentAiMarkdown.length,
        finalArgLen: finalText != null ? String(finalText).length : 0,
        filledFromFinal: !!(finalText && !currentAiMarkdown)
    });

    if (finalText && !currentAiMarkdown && finalText !== '__stopped__') {
        currentAiMarkdown = String(finalText);
    }

    var hadContent = !!currentAiMarkdown;

    // Save the final AI response to the active tab before clearing
    if (hadContent) {
        addMessageToActiveTab('assistant', currentAiMarkdown);
    }

    cancelStreamMarkdownFlush();

    if (hadContent) {
        applyStreamMarkdownFlush();
    }

    // FIND and update the placeholder if no content arrived (error/stop before first chunk)
    if (!hadContent) {
        var placeholderEl = chatHistory.querySelector('.ai-generating-root');
        if (placeholderEl) {
            var msgEl = placeholderEl.closest('.ai-message');
            if (msgEl) {
                if (finalText === '__stopped__') {
                    msgEl.innerHTML = '<div style="font-size:11px;color:var(--text3);font-style:normal;padding:4px 0;">⏸ Stopped</div>';
                } else if (finalText && String(finalText).indexOf('__error__:') === 0) {
                    // Extract error text directly from the parameter
                    var errText = String(finalText).slice(9); // remove '__error__:' prefix
                    msgEl.innerHTML = '<div style="font-size:12px;color:var(--red);font-style:normal;padding:4px 0;">&#10060; ' + escapeHtml('Error: ' + errText) + '</div>';
                } else {
                    // Empty or falsy finalText (no content arrived from API) — show warning
                    msgEl.innerHTML = '<div style="font-size:12px;color:var(--text2);font-style:normal;padding:8px 0;text-align:center;">'
                        + '⚠️ AI повернуло порожню відповідь.<br>'
                        + '<span style="font-size:11px;color:var(--text3);">Спробуйте переформулювати запит або перевірте консоль (F12 → Console) для деталей.</span>'
                        + '</div>';
                    aiEditorDebug('finish_ai_message_empty_response', {
                        finalArgLen: finalText != null ? String(finalText).length : 0,
                        streamChunks: aiStreamChunkNum
                    });
                }
            }
        }
    }

    currentAiMessageDiv = null;
    currentAiMarkdown = '';
    aiStreamChunkNum = 0;

    setGeneratingState(false);
    updatePendingActionsBar();

    aiEditorDebug('finish_ai_message_done', {});
    inputEl.focus();
};

window.on_ai_error = function (msg) {
    aiEditorDebug('on_ai_error', {
        msgLen: (msg != null ? String(msg).length : 0),
        preview: String(msg || '').slice(0, 200)
    });
    addMessageToActiveTab('system', 'Error: ' + msg, { isError: true });
    appendSystemMessage('Error: ' + msg, true);
    window.finish_ai_message('__error__:' + (msg || ''));
    setAiErrorState(true);
};

window.set_chat_input_text = function (text) {
    inputEl.value = text || '';
    inputEl.scrollTop = 0;
    resizeChatInput();
    inputEl.focus();
};

window.set_chart_selection_context = function (payload) {
    chartSelectionContext = payload;
    appendSystemMessage('Chart area captured and attached to AI context.');
};

// Mirror-only: render to DOM without saving (sync is handled by chat_tabs_state)
window.appendUserMessage = function (text) {
    const escaped = (text || '').replace(/\n/g, '<br>');
    chatHistory.innerHTML += `<div class="message user-message">${escaped}</div>`;
    scrollChatToBottom();
};

window.appendAiMessage = function (fullText) {
    if (!fullText) return;
    const html = marked.parse(fullText);
    chatHistory.innerHTML += `<div class="message ai-message">${html}</div>`;
    scrollChatToBottom();
};

window.clear_chart_selection_context = function () {
    chartSelectionContext = null;
    appendSystemMessage('Chart area context cleared.');
};

window.toggleChatPanel = function () {
    if (_panelCollapsed === 'chat' || !chatContainer.style.display || chatContainer.style.display === 'none') {
        // Chat is hidden — restore it
        _panelCollapsed = null;
        chatContainer.style.display = 'flex';
        chatContainer.style.width = ''; // reset to flex sizing
        const total = window.innerWidth;
        const restoreW = Math.min(CHAT_MIN + SNAP_THRESHOLD * 2, Math.floor(total * 0.35));
        leftPanel.style.width = Math.max(EDITOR_MIN, total - restoreW) + 'px';
    } else {
        // Chat is visible — collapse it
        _panelCollapsed = 'chat';
        chatContainer.style.display = 'none';
        leftPanel.style.width = '100%';
    }
    if (editor) setTimeout(() => editor.layout(), 50);
};

window.restoreEditorPanel = function () {
    // Restore editor panel from collapsed state
    if (_panelCollapsed === 'editor') {
        _panelCollapsed = null;
        leftPanel.style.display = 'flex';
        const total = window.innerWidth;
        const restoreW = Math.min(EDITOR_MIN + SNAP_THRESHOLD * 2, Math.floor(total * 0.65));
        leftPanel.style.width = restoreW + 'px';
        chatContainer.style.width = ''; // reset to flex sizing
        if (editor) setTimeout(() => editor.layout(), 50);
    }
};

window.set_chat_only_mode = function (enabled) {
    const editorContainer = document.getElementById('editor-container');
    const resizerEl = document.getElementById('resizer');
    const chatContainerEl = document.getElementById('chat-container');
    const topBar = document.getElementById('top-bar');

    if (enabled) {
        editorContainer.style.display = 'none';
        resizerEl.style.display = 'none';
        chatContainerEl.style.width = '100%';
        if (topBar) topBar.style.display = 'none';
    } else {
        editorContainer.style.display = 'flex';
        resizerEl.style.display = 'block';
        chatContainerEl.style.width = 'auto';
        if (topBar) topBar.style.display = 'flex';
    }

    if (editor) {
        setTimeout(() => editor.layout(), 100);
    }
};

window.append_ai_error_ask = function (traceback) {
    addMessageToActiveTab('user', 'Fix this error:\n' + traceback);
    const escapedTraceback = escapeHtml(traceback);
    chatHistory.innerHTML += `<div class="message user-message">Fix this error:<br><pre style="background:var(--bg);padding:8px;border-radius:4px;overflow-x:auto;">${escapedTraceback}</pre></div>`;

    currentAiMessageDiv = document.createElement('div');
    currentAiMessageDiv.className = 'message ai-message';
    currentAiMessageDiv.innerHTML = buildGeneratingPlaceholderHtml('Auto-fix…');
    chatHistory.appendChild(currentAiMessageDiv);

    currentAiMarkdown = '';
    scrollChatToBottom();
    setGeneratingState(true);

    previewBaselineCode = getEditorValue();
    pendingCodeCandidate = '';
    pendingPatchChunks = null;
    pendingPatchPreviewResult = null;
    updatePendingActionsBar();
};

function configureMarked() {
    marked.setOptions({
        highlight: function (code, lang) {
            if (lang && hljs.getLanguage(lang)) {
                try {
                    return hljs.highlight(code, { language: lang }).value;
                } catch (e) { }
            }
            try {
                return hljs.highlightAuto(code).value;
            } catch (e) { }
            return escapeHtml(code);
        },
        breaks: true,
        gfm: true
    });

    const renderer = new marked.Renderer();
    renderer.code = function (code, lang) {
        const validLang = !!(lang && hljs.getLanguage(lang));
        let highlighted;

        try {
            highlighted = validLang
                ? hljs.highlight(code, { language: lang }).value
                : hljs.highlightAuto(code).value;
        } catch (e) {
            highlighted = escapeHtml(code);
        }

        const escapedCode = code
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');

        return `
    <div class="code-header">
        <span>${escapeHtml(lang || 'python')}</span>
        <button class="btn apply-btn" onclick="applyCodeToEditor(this)">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor">
                <path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/>
            </svg>
            Apply to Editor
        </button>
    </div>
    <pre><code class="hljs ${escapeHtml(lang)}">${highlighted}</code><textarea style="display:none;" class="raw-code">${escapedCode}</textarea></pre>
`;
    };

    marked.use({ renderer });
}

// ============= MAIN INITIALIZATION =============
