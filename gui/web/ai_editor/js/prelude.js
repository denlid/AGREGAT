// Early Qt bridge stubs.
//
// PySide's QWebEngineView can run JavaScript from AIAssistantEditor as soon as
// the page starts loading, while editor.js/chat.js are still waiting on Monaco
// and QWebChannel initialization. These tiny no-op/default handlers keep those
// early Python calls safe. The real implementations intentionally replace them
// later in editor.js and chat.js.
//
// Keep this file loaded before state.js, editor.js, and chat.js in index.html.
window.__aiEditorPreludeQueue = window.__aiEditorPreludeQueue || {
    chatTabsStateJson: null,
    aiPhaseText: null
};

window.set_strategy_header_title = function (title) {
    var el = document.getElementById('editor-header-title');
    if (el) el.textContent = (title && String(title).trim()) ? String(title).trim() : 'New strategy';
};
window.setAiPhaseMessage = function (text) {
    window.__aiEditorPreludeQueue.aiPhaseText = text;
};
window.applyChatTabsState = function (stateJson) {
    window.__aiEditorPreludeQueue.chatTabsStateJson = stateJson;
};
