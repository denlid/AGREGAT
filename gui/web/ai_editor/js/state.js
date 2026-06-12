'use strict';

let editor = null;
let pyBridge = null;
let bridgeReady = false;
let currentAiMessageDiv = null;
let currentAiMarkdown = '';
let isGenerating = false;
let isAiErrorState = false;
let lastAiRequest = null;
let chartSelectionContext = null;
let selectedAttachments = [];
let pendingCodeCandidate = '';
let pendingPatchChunks = null;
let pendingPatchPreviewResult = null;
let previewBaselineCode = '';
let previewDecorations = [];
let previewViewZoneIds = [];
let streamFlushRaf = 0;
let aiEditorDebugSeq = 0;
let aiStreamChunkNum = 0;
let markdownFlushCount = 0;
let lastFlushTime = 0;
const FLUSH_THROTTLE_MS = 100;
let _suppressEditorDirty = false;
let _editorDiagnosticsTimer = 0;
let _panelCollapsed = null; // null = none, 'editor' = editor hidden, 'chat' = chat hidden
const EDITOR_MIN = 200;
const CHAT_MIN = 250;
const SNAP_THRESHOLD = 60; // extra pixels past min to trigger collapse/restore
let canUseVision = true; // (4.6) whether current model supports images

// ============= AI EDIT HISTORY (3.2) =============
const aiEditHistory = [];
const MAX_AI_HISTORY = 50;
let aiHistoryIndex = -1;
let _diffToggleOn = false; // (3.1) side-by-side diff toggle
let _accumulatedPatchRaw = ''; // (2.8) incremental patch builder

// ============= CHAT TABS STATE =============
let chatTabs = [];            // { id, label, messages: [{role, content}] }
let activeChatTabId = null;
let tabIdCounter = 0;
const CHAT_PERSIST_KEY = 'agregat_chat_tabs_v1';
let _suppressChatTabsSync = false;  // prevent echo loops during cross-panel sync
let _localStorageAvailable = false;

// Check localStorage availability once (QtWebEngine may block it)
try {
    const testKey = '__agr_storage_test__';
    localStorage.setItem(testKey, '1');
    localStorage.removeItem(testKey);
    _localStorageAvailable = true;
} catch (e) {
    _localStorageAvailable = false;
    console.warn('localStorage not available, using in-memory persistence only');
}

// ============= DOM ELEMENTS =============
const inputEl = document.getElementById('chat-input');
const attachBtn = document.getElementById('attach-btn');
const attachMenu = document.getElementById('attach-menu');
const pendingActionsEl = document.getElementById('input-pending-actions');
const actionBtn = document.getElementById('action-btn');
const iconSend = document.getElementById('icon-send');
const iconStop = document.getElementById('icon-stop');
const iconReload = document.getElementById('icon-reload');
const chatHistory = document.getElementById('chat-history');
const resizer = document.getElementById('resizer');
const leftPanel = document.getElementById('editor-container');
const chatContainer = document.getElementById('chat-container');
const monacoEditEl = document.getElementById('monaco-edit');

// ============= CHAT TABS — PERSISTENCE =============
