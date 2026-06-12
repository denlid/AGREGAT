function escapeHtml(s) {
    const str = String(s || '');
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function normalizeText(s) {
    return (s || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
}

function sanitizeHTML(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ============= AI DEBUG (browser console only; DevTools → Console) =============
function aiEditorDebug(evt, detail) {
    try {
        aiEditorDebugSeq += 1;
        const payload = { evt, seq: aiEditorDebugSeq, ts: Date.now() };
        if (detail && typeof detail === 'object') {
            Object.keys(detail).forEach(k => {
                if (Object.prototype.hasOwnProperty.call(detail, k)) {
                    payload[k] = detail[k];
                }
            });
        }
        console.debug('[ai_editor]', payload);
    } catch (e) { /* ignore */ }
}

// ============= MONACO EDITOR HELPERS =============
