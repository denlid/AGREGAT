// Monaco editor, diagnostics, strategy title, patch preview, and code apply/save.

function isEditorReady() {
    return editor && editor.getModel && !editor.getModel().isDisposed();
}

function safeEditorSetValue(text) {
    if (!isEditorReady()) {
        console.warn('Editor not ready for setValue');
        return false;
    }

    try {
        const normalizedText = normalizeText(text || '');
        editor.setValue(normalizedText);

        // CRITICAL FIX for QtWebEngine: force layout update
        editor.layout();

        // Force Monaco to process the change
        editor.revealLine(Math.min(editor.getModel().getLineCount(), 1));

        // Additional QtWebEngine hack: trigger reflow
        monacoEditEl.style.transform = 'translateZ(0)';
        monacoEditEl.offsetHeight; // Force reflow
        monacoEditEl.style.transform = '';

        return true;
    } catch (e) {
        console.error('Failed to set editor value:', e);
        return false;
    }
}

function getEditorValue() {
    return isEditorReady() ? editor.getValue() : '';
}

function applyEditorDiagnostics(diagnostics) {
    if (!isEditorReady() || !window.monaco) return;
    const model = editor.getModel();
    if (!model) return;

    const markers = Array.isArray(diagnostics) ? diagnostics.map(function (item) {
        const severityName = String(item.severity || 'error').toLowerCase();
        const severity = severityName === 'warning'
            ? monaco.MarkerSeverity.Warning
            : monaco.MarkerSeverity.Error;
        return {
            severity: severity,
            message: String(item.message || 'Syntax error'),
            startLineNumber: Math.max(Number(item.startLineNumber || 1), 1),
            startColumn: Math.max(Number(item.startColumn || 1), 1),
            endLineNumber: Math.max(Number(item.endLineNumber || item.startLineNumber || 1), 1),
            endColumn: Math.max(Number(item.endColumn || item.startColumn || 2), 1),
            source: 'AGREGAT'
        };
    }) : [];

    monaco.editor.setModelMarkers(model, 'agregat-python-syntax', markers);
}

// ============= VIEW ZONES MANAGEMENT =============
function getEditorLineHeightPx() {
    if (!isEditorReady()) return 19;
    try {
        return editor.getOption(monaco.editor.EditorOption.lineHeight) || 19;
    } catch (e) {
        return 19;
    }
}

function clearPreviewViewZones() {
    if (!previewViewZoneIds.length) {
        previewViewZoneIds = [];
        return;
    }

    if (!isEditorReady()) {
        previewViewZoneIds = [];
        return;
    }

    const ids = previewViewZoneIds.slice();
    previewViewZoneIds = [];

    try {
        editor.changeViewZones(accessor => {
            ids.forEach(id => {
                try { accessor.removeZone(id); } catch (e) { }
            });
        });
    } catch (e) {
        console.error('Failed to clear view zones:', e);
    }
}

function buildDeletedZoneDom(lines) {
    const root = document.createElement('div');
    root.className = 'ai-deleted-zone';
    root.setAttribute('aria-hidden', 'true');

    // Показуємо видалений код — такий же розмір/шрифт як в звичайному коді
    if (lines && lines.length > 0) {
        const pre = document.createElement('pre');
        const code = document.createElement('code');

        const maxLines = 15;
        let displayLines = lines.slice(0, maxLines);
        let truncated = lines.length > maxLines;

        let text = displayLines.join('\n');
        if (truncated) {
            text += '\n... (' + (lines.length - maxLines) + ' more lines)';
        }
        code.textContent = text;
        pre.appendChild(code);
        root.appendChild(pre);
    }

    return root;
}

// ============= DIFF ALGORITHM =============
function diffLineOps(oldLines, newLines) {
    const m = oldLines.length;
    const n = newLines.length;

    // Use simpler diff for large files
    if (m * n > 100000) {
        return simpleDiff(oldLines, newLines);
    }

    const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));

    for (let i = 1; i <= m; i++) {
        for (let j = 1; j <= n; j++) {
            if (oldLines[i - 1] === newLines[j - 1]) {
                dp[i][j] = dp[i - 1][j - 1] + 1;
            } else {
                dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
            }
        }
    }

    const ops = [];
    let i = m, j = n;

    while (i > 0 || j > 0) {
        if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
            ops.unshift({ type: 'eq', oi: i - 1, ni: j - 1 });
            i--; j--;
        } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
            ops.unshift({ type: 'ins', ni: j - 1 });
            j--;
        } else {
            ops.unshift({ type: 'del', oi: i - 1 });
            i--;
        }
    }

    return ops;
}

function simpleDiff(oldLines, newLines) {
    const ops = [];
    let i = 0, j = 0;

    while (i < oldLines.length || j < newLines.length) {
        if (i < oldLines.length && j < newLines.length && oldLines[i] === newLines[j]) {
            ops.push({ type: 'eq', oi: i, ni: j });
            i++; j++;
        } else if (j < newLines.length && (i >= oldLines.length || oldLines[i] !== newLines[j])) {
            ops.push({ type: 'ins', ni: j });
            j++;
        } else if (i < oldLines.length) {
            ops.push({ type: 'del', oi: i });
            i++;
        }
    }

    return ops;
}

function analyzePatchLineDiff(beforeText, afterText) {
    const oldLines = normalizeText(beforeText || '').split('\n');
    const newLines = normalizeText(afterText || '').split('\n');
    const ops = diffLineOps(oldLines, newLines);

    const deleteZones = [];
    const greenLines = new Set();
    let anchorAfter = 0;
    let delBuffer = [];

    function flushDeletes() {
        if (!delBuffer.length) return;
        deleteZones.push({
            afterLineNumber: anchorAfter,
            lines: delBuffer.slice()
        });
        delBuffer = [];
    }

    for (const op of ops) {
        if (op.type === 'eq') {
            flushDeletes();
            anchorAfter = op.ni + 1;
        } else if (op.type === 'ins') {
            flushDeletes();
            greenLines.add(op.ni + 1);
            anchorAfter = op.ni + 1;
        } else {
            delBuffer.push(oldLines[op.oi]);
        }
    }
    flushDeletes();

    return { deleteZones, greenLines };
}

function highlightPatchPreviewDiff(beforeText, afterText) {
    if (!isEditorReady()) return;

    clearPreviewViewZones();

    const afterNorm = normalizeText(afterText || '');
    const beforeNorm = normalizeText(beforeText || '');
    const { deleteZones, greenLines } = analyzePatchLineDiff(beforeNorm, afterNorm);

    const decs = [];
    greenLines.forEach(line => {
        decs.push({
            range: new monaco.Range(line, 1, line, 1),
            options: {
                isWholeLine: true,
                className: 'ai-line-preview',
                linesDecorationsClassName: 'ai-line-preview-glyph'
            }
        });
    });

    try {
        previewDecorations = editor.deltaDecorations(previewDecorations, decs);
    } catch (e) {
        console.error('Failed to set decorations:', e);
    }

    if (!deleteZones.length) return;

    const lh = getEditorLineHeightPx();
    deleteZones.sort((a, b) => a.afterLineNumber - b.afterLineNumber);

    try {
        editor.changeViewZones(accessor => {
            deleteZones.forEach(block => {
                const n = block.lines.length;
                if (!n) return;

                const dom = buildDeletedZoneDom(block.lines);  // ← ПЕРЕДАЄМО ТЕКСТ

                // Вираховуємо висоту: мінімум 20px на рядок, максимум 400px
                const lineHeight = 18;
                const padding = 16;
                const inner = Math.min(400, Math.max(20, n * lineHeight + padding));

                const id = accessor.addZone({
                    afterLineNumber: block.afterLineNumber,
                    heightInPx: inner,
                    suppressMouseDown: false,
                    domNode: dom
                });

                previewViewZoneIds.push(id);
            });
        });
    } catch (e) {
        console.error('Failed to add view zones:', e);
    }
}

// ============= PATCH FUNCTIONS =============
function findBeforeSpan(srcRaw, beforeRaw) {
    const src = normalizeText(srcRaw);
    const before = normalizeText(beforeRaw);

    if (!before) return { ok: true, start: 0, end: 0, srcNorm: src };

    let k = src.indexOf(before);
    if (k >= 0) return { ok: true, start: k, end: k + before.length, srcNorm: src };

    let bt = before;
    while (bt.startsWith('\n')) bt = bt.slice(1);
    while (bt.endsWith('\n')) bt = bt.slice(0, -1);

    const patLines = bt.split('\n');
    if (!patLines.length || (patLines.length === 1 && patLines[0] === '')) {
        return { ok: false, srcNorm: src };
    }

    function rowEqualLoose(a, b) {
        if (a === b) return true;
        const ra = (a || '').replace(/\s+$/g, '');
        const rb = (b || '').replace(/\s+$/g, '');
        if (ra === rb) return true;
        if ((a || '').trim() === (b || '').trim()) return true;

        const tab4 = s => s.replace(/\t/g, '    ');
        if (tab4(ra) === tab4(rb)) return true;
        return tab4(a || '').trim() === tab4(b || '').trim();
    }

    const srcLines = src.split('\n');
    const candidates = [];

    for (let i = 0; i <= srcLines.length - patLines.length; i++) {
        let matched = true;
        for (let j = 0; j < patLines.length; j++) {
            if (!rowEqualLoose(srcLines[i + j] ?? '', patLines[j])) {
                matched = false;
                break;
            }
        }
        if (matched) candidates.push(i);
    }

    if (candidates.length !== 1) return { ok: false, srcNorm: src };

    const idx = candidates[0];
    let start = 0;
    for (let t = 0; t < idx; t++) start += srcLines[t].length + 1;

    let end = start;
    for (let j = 0; j < patLines.length; j++) {
        end += srcLines[idx + j].length;
        if (j < patLines.length - 1) end += 1;
    }

    return { ok: true, start, end, srcNorm: src };
}

function applyOnePatchToText(text, patch) {
    const srcRaw = text || '';
    const before = patch.before || '';
    const after = patch.after || '';

    if (!before && !after) return { ok: false, text: normalizeText(srcRaw) };

    if (!before && after) {
        const srcN = normalizeText(srcRaw);
        const add = normalizeText(after);
        const glue = srcN.endsWith('\n') || !srcN ? '' : '\n';
        return { ok: true, text: srcN + glue + add };
    }

    const span = findBeforeSpan(srcRaw, before);
    if (!span.ok) return { ok: false, text: normalizeText(srcRaw) };

    const srcN = span.srcNorm;
    return {
        ok: true,
        text: srcN.slice(0, span.start) + normalizeText(after) + srcN.slice(span.end)
    };
}

function applyAllPatchesToText(baseText, chunks) {
    let t = normalizeText(baseText);

    for (let i = 0; i < chunks.length; i++) {
        const res = applyOnePatchToText(t, chunks[i]);
        if (!res.ok) return { ok: false, text: t, failedIndex: i };
        t = res.text;
    }

    return { ok: true, text: t };
}

function parsePatchChunks(rawPatchText) {
    const src = normalizeText(rawPatchText || '').replace(/\s+$/, '');
    if (!src.trim()) return [];

    const chunks = [];
    let current = [];

    for (const ln of src.split('\n')) {
        if (/^@{3,}\s*$/.test(ln.trim())) {
            const block = current.join('\n').trimEnd();
            if (block) chunks.push(block);
            current = [];
            continue;
        }
        current.push(ln);
    }

    const tail = current.join('\n').trimEnd();
    if (tail) chunks.push(tail);

    const parsed = [];
    for (const chunk of chunks) {
        const beforeLines = [];
        const afterLines = [];

        for (const line of chunk.split('\n')) {
            const m = line.match(/^\s*([+-])(.*)$/);
            if (!m) continue;
            const sign = m[1];
            const payload = m[2] != null ? m[2] : '';

            if (sign === '-') beforeLines.push(payload);
            else afterLines.push(payload);
        }

        if (!beforeLines.length && !afterLines.length) continue;

        parsed.push({
            before: beforeLines.join('\n'),
            after: afterLines.join('\n')
        });
    }

    return parsed;
}

function extractLatestCodeBlock(markdownText) {
    if (!markdownText) return '';

    const tagged = [...markdownText.matchAll(/```(?:python|py|python3)\s*([\s\S]*?)```/gi)];
    if (tagged.length > 0) {
        const t = normalizeText(tagged[tagged.length - 1][1] || '')
            .replace(/^\n+/, '')
            .replace(/\n+$/, '');
        if (t.trim()) return t;
    }

    const any = [...markdownText.matchAll(/```(\w*)\s*([\s\S]*?)```/g)];
    for (let i = any.length - 1; i >= 0; i--) {
        const lang = (any[i][1] || '').toLowerCase();
        const body = normalizeText(any[i][2] || '').replace(/^\n+/, '').replace(/\n+$/, '');

        if (!body.trim()) continue;
        if (lang === 'agregat_patch' || lang === 'json') continue;
        if (!lang || lang === 'code' || lang === 'text' || lang === 'plaintext') {
            if (/\b(class|def|import|from)\b/.test(body)) return body;
        }
    }

    return '';
}

function extractLatestPatchBlock(markdownText) {
    if (!markdownText) return '';

    const blocks = [...markdownText.matchAll(/```agregat_patch\s*([\s\S]*?)```/gi)];
    if (!blocks.length) return '';

    let raw = blocks[blocks.length - 1][1] || '';
    raw = normalizeText(raw).replace(/^\n+/, '').replace(/\n+$/, '');
    return raw;
}

function tryConsumeLatestPatch(markdownText) {
    const raw = extractLatestPatchBlock(markdownText);
    const chunks = parsePatchChunks(raw);

    if (!chunks.length) {
        pendingPatchChunks = null;
        pendingPatchPreviewResult = null;
        return false;
    }

    const base = normalizeText(previewBaselineCode || getEditorValue());
    const r = applyAllPatchesToText(base, chunks);

    if (!r.ok) {
        aiEditorDebug('patch_apply_failed', {
            chunks: chunks.length,
            failedIndex: r.failedIndex != null ? r.failedIndex : -1
        });
        pendingPatchChunks = null;
        pendingPatchPreviewResult = null;
        return false;
    }

    pendingPatchChunks = chunks;
    pendingPatchPreviewResult = r.text;
    return true;
}

// ============= APPLY / PREVIEW LOGIC =============
function resolveApplyBody(candidateText) {
    const base = normalizeText(previewBaselineCode || '');
    const ed = normalizeText(getEditorValue());
    const cand = normalizeText(candidateText != null ? candidateText : '');

    if (!cand) return ed;
    if (ed === base) return cand;
    if (ed !== cand) return ed;
    return cand;
}

function applyStreamedCodePreview(codeText) {
    if (!isEditorReady() || codeText === undefined || codeText === null) return;

    const next = normalizeText(String(codeText)).replace(/^\n+/, '').replace(/\n+$/, '');
    const before = getEditorValue();

    if (before !== next) {
        safeEditorSetValue(next);
    }

    highlightPatchPreviewDiff(previewBaselineCode || before, next);
}

// ============= AI EDIT HISTORY (3.2) =============
function saveEditSnapshot(prompt) {
    const code = previewBaselineCode || getEditorValue();
    if (!code) return;
    
    // Discard future entries if we undid
    if (aiHistoryIndex < aiEditHistory.length - 1) {
        aiEditHistory.splice(aiHistoryIndex + 1);
    }
    
    aiEditHistory.push({
        timestamp: Date.now(),
        code: code,
        prompt: (prompt || '').slice(0, 80)
    });
    
    if (aiEditHistory.length > MAX_AI_HISTORY) {
        aiEditHistory.shift();
    }
    aiHistoryIndex = aiEditHistory.length - 1;
}

function undoAIAction() {
    if (aiHistoryIndex <= 0) return;
    aiHistoryIndex--;
    const snapshot = aiEditHistory[aiHistoryIndex];
    safeEditorSetValue(snapshot.code);
    previewBaselineCode = snapshot.code;
    clearPreviewViewZones();
    appendSystemMessage('↩ Undo to: ' + snapshot.prompt);
}

function redoAIAction() {
    if (aiHistoryIndex >= aiEditHistory.length - 1) return;
    aiHistoryIndex++;
    const snapshot = aiEditHistory[aiHistoryIndex];
    safeEditorSetValue(snapshot.code);
    previewBaselineCode = snapshot.code;
    clearPreviewViewZones();
    appendSystemMessage('↪ Redo to: ' + snapshot.prompt);
}

function applyPendingChanges() {
    if (!isEditorReady()) {
        appendSystemMessage('Editor not ready. Wait and try again.', true);
        return;
    }

    var applied = false;

    if (pendingPatchChunks && pendingPatchChunks.length && pendingPatchPreviewResult != null) {
        const body = resolveApplyBody(pendingPatchPreviewResult);

        if (safeEditorSetValue(body)) {
            saveEditSnapshot('Patch applied');
            previewBaselineCode = body;
            clearPreviewViewZones();
            previewDecorations = editor.deltaDecorations(previewDecorations, []);
            pendingPatchChunks = null;
            pendingPatchPreviewResult = null;
            pendingCodeCandidate = '';
            appendSystemMessage('Patch applied.');
            updatePendingActionsBar();
            applied = true;
        } else {
            appendSystemMessage('Failed to apply patch. Editor error.', true);
        }
    } else if (pendingCodeCandidate) {
        const body = resolveApplyBody(pendingCodeCandidate);

        if (safeEditorSetValue(body)) {
            saveEditSnapshot('Code applied from AI');
            previewBaselineCode = body;
            clearPreviewViewZones();
            previewDecorations = editor.deltaDecorations(previewDecorations, []);
            pendingCodeCandidate = '';
            appendSystemMessage('Saved to editor.');
            updatePendingActionsBar();
            applied = true;
        } else {
            appendSystemMessage('Failed to save to editor.', true);
        }
    } else {
        appendSystemMessage('Nothing to apply.', true);
    }

    // Auto-save to disk via Python bridge
    if (applied) {
        safeBridgeCall('receive_save_strategy', getEditorValue());
    }
}

function rejectPendingCodePreview() {
    if (!isEditorReady()) return;

    safeEditorSetValue(previewBaselineCode || '');
    clearPreviewViewZones();
    previewDecorations = editor.deltaDecorations(previewDecorations, []);
    pendingCodeCandidate = '';
    pendingPatchChunks = null;
    pendingPatchPreviewResult = null;
    updatePendingActionsBar();
    appendSystemMessage('Preview cancelled.');
}

function updatePendingActionsBar() {
    if (!pendingActionsEl) return;

    const show = !!(pendingCodeCandidate ||
        (pendingPatchChunks && pendingPatchChunks.length && pendingPatchPreviewResult != null));

    pendingActionsEl.style.display = show ? 'flex' : 'none';
}

// ============= CHAT FUNCTIONS =============

window.set_editor_value = function (text) {
    if (!isEditorReady()) return;
    _suppressEditorDirty = true;
    try {
        if (safeEditorSetValue(text)) {
            previewBaselineCode = getEditorValue();
            pendingCodeCandidate = '';
            pendingPatchChunks = null;
            pendingPatchPreviewResult = null;
            clearPreviewViewZones();
            previewDecorations = editor.deltaDecorations(previewDecorations, []);
            updatePendingActionsBar();
        }
    } finally {
        setTimeout(function () { _suppressEditorDirty = false; }, 0);
    }
};

window.set_editor_diagnostics = function (diagnostics) {
    applyEditorDiagnostics(diagnostics);
};

window.apply_app_theme = function (theme) {
    if (!theme) return;
    const root = document.documentElement;
    const map = {
        bg: '--bg',
        surface: '--surface',
        elevated: '--elevated',
        border: '--border',
        accent: '--accent',
        accentLight: '--accent-light',
        text: '--text',
        text2: '--text2',
        text3: '--text3',
        red: '--red',
        green: '--green'
    };
    Object.keys(map).forEach(function (key) {
        if (theme[key]) root.style.setProperty(map[key], theme[key]);
    });
    if (theme.monacoTheme) {
        root.dataset.monacoTheme = theme.monacoTheme;
    }
    if (window.monaco && editor && theme.monacoTheme) {
        monaco.editor.setTheme(theme.monacoTheme);
    }
};

function notifyEditorDirty() {
    if (_suppressEditorDirty) return;
    if (_editorDiagnosticsTimer) {
        clearTimeout(_editorDiagnosticsTimer);
    }
    _editorDiagnosticsTimer = setTimeout(function () {
        _editorDiagnosticsTimer = 0;
        if (pyBridge && typeof pyBridge.receive_editor_code_changed === 'function') {
            pyBridge.receive_editor_code_changed(getEditorValue());
        } else if (pyBridge && typeof pyBridge.receive_editor_dirty === 'function') {
            pyBridge.receive_editor_dirty();
        }
    }, 250);
    if (pyBridge && typeof pyBridge.receive_editor_dirty === 'function') {
        pyBridge.receive_editor_dirty();
    }
}

window.set_strategy_header_title = function (title) {
    var el = document.getElementById('editor-header-title');
    if (!el) {
        // DOM not ready yet — retry once after microtask
        var t = title;
        setTimeout(function () {
            var el2 = document.getElementById('editor-header-title');
            if (el2) el2.textContent = (t && String(t).trim()) ? String(t).trim() : 'New strategy';
        }, 0);
        return;
    }
    el.textContent = (title && String(title).trim()) ? String(title).trim() : 'New strategy';
};

// ============= STRATEGY RENAME FUNCTIONS =============
window.startEditingTitle = function () {
    const titleSpan = document.getElementById('editor-header-title');
    const container = document.getElementById('editor-title-container');
    if (!titleSpan || !container) return;

    const currentTitle = titleSpan.textContent.trim();
    // Remove asterisk if present
    const cleanTitle = currentTitle.replace(/\s*\*\s*$/, '');

    // Create input element
    const input = document.createElement('input');
    input.type = 'text';
    input.id = 'editor-title-input';
    input.value = cleanTitle;
    input.className = 'header-title';

    // Replace span with input
    container.replaceChild(input, titleSpan);
    input.focus();
    input.select();

    // Handle Enter key
    input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            finishEditingTitle(input, titleSpan);
        } else if (e.key === 'Escape') {
            e.preventDefault();
            cancelEditingTitle(input, titleSpan);
        }
    });

    // Handle blur (click outside)
    input.addEventListener('blur', function () {
        setTimeout(function () {
            finishEditingTitle(input, titleSpan);
        }, 100);
    });
};

function finishEditingTitle(input, originalSpan) {
    const container = document.getElementById('editor-title-container');
    if (!container || !input.parentNode) return;

    const newName = input.value.trim();
    if (!newName) {
        cancelEditingTitle(input, originalSpan);
        return;
    }

    // Send to Python
    if (safeBridgeCall('receive_rename_strategy', newName)) {
        // Python will call set_strategy_header_title to update the UI
        container.replaceChild(originalSpan, input);
    } else {
        cancelEditingTitle(input, originalSpan);
    }
}

function cancelEditingTitle(input, originalSpan) {
    const container = document.getElementById('editor-title-container');
    if (!container || !input.parentNode) return;
    container.replaceChild(originalSpan, input);
}

window.show_rename_error = function (msg) {
    appendSystemMessage('Rename failed: ' + msg, true);
};

window.saveStrategy = function () {
    safeBridgeCall('receive_save_strategy', getEditorValue());
};

window.applyCodeToEditor = function (btn) {
    const rawCodeEl = btn.parentElement.nextElementSibling.querySelector('.raw-code');
    if (rawCodeEl && isEditorReady()) {
        let code = rawCodeEl.value;
        const txt = document.createElement('textarea');
        txt.innerHTML = code;
        code = txt.value;

        previewBaselineCode = getEditorValue();

        if (safeEditorSetValue(code)) {
            pendingCodeCandidate = '';
            pendingPatchChunks = null;
            pendingPatchPreviewResult = null;
            clearPreviewViewZones();
            previewDecorations = editor.deltaDecorations(previewDecorations, []);
            appendSystemMessage('Code pasted into editor.');
            updatePendingActionsBar();
        }
    }
};

function initEditor() {
    try {
        editor = monaco.editor.create(monacoEditEl, {
            value: [
                'from engine.strategy.context import Strategy',
                '',
                'class AIStrategy(Strategy):',
                '    name = "AI Strategy"',
                '    ',
                '    def on_candle(self, ctx):',
                '        pass'
            ].join('\n'),
            language: 'python',
            theme: (document.documentElement.dataset.monacoTheme || 'vs-dark'),
            automaticLayout: true,
            minimap: { enabled: true },
            fontSize: 13,
            fontFamily: "'Roboto Mono', Consolas, monospace",
            scrollBeyondLastLine: false,
            padding: { top: 15 },
            // QtWebEngine optimizations
            renderWhitespace: 'none',
            renderControlCharacters: false,
            renderIndentGuides: false,
            'semanticHighlighting.enabled': true,
            smoothScrolling: false,
            cursorBlinking: 'solid',
            cursorSmoothCaretAnimation: false
        });

        editor.onDidChangeModelContent(function () {
            notifyEditorDirty();
        });

        // Force initial layout for QtWebEngine
        setTimeout(() => {
            if (editor) {
                editor.layout();
                editor.revealLine(1);
            }
        }, 50);

        // Additional resize observer for reliability
        if (window.ResizeObserver) {
            const ro = new ResizeObserver(() => {
                if (editor) editor.layout();
            });
            ro.observe(monacoEditEl);
        }

        console.log('Monaco Editor initialized successfully');
        return true;
    } catch (e) {
        console.error('Failed to initialize Monaco Editor:', e);
        return false;
    }
}

function initBridge() {
    if (typeof QWebChannel === 'undefined') {
        console.warn('QWebChannel not loaded, retrying...');
        setTimeout(initBridge, 200);
        return;
    }

    try {
        new QWebChannel(qt.webChannelTransport, function (channel) {
            pyBridge = channel.objects.bridge;
            bridgeReady = true;

            if (isEditorReady()) {
                pyBridge.editor_ready();
                aiEditorDebug('webchannel_ready', { editorReady: true });
            } else {
                aiEditorDebug('webchannel_ready', { editorReady: false });
                // Editor might still be loading, Python side should handle this
                setTimeout(() => {
                    if (isEditorReady()) {
                        pyBridge.editor_ready();
                    }
                }, 300);
            }
        });
    } catch (e) {
        console.error('QWebChannel initialization failed:', e);
        setTimeout(initBridge, 500);
    }
}
