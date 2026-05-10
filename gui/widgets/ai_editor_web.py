import logging
import json
from PySide6.QtCore import QObject, Signal, Slot, Qt
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

logger = logging.getLogger("StrategyTester.gui.widgets.ai_editor_web")

WEB_APP_HTML = r"""
<!DOCTYPE html>
<html>
<head>
    <meta http-equiv="Content-Type" content="text/html;charset=utf-8" />
    <style>
        :root {
            --bg: #0B0B0D;
            --surface: #131316;
            --elevated: #1A1A1F;
            --border: #252530;
            --accent: #2962FF;
            --accent-light: #5C8AFF;
            --text: #E8E8EE;
            --text2: #8A8A96;
            --text3: #55555F;
            --red: #EF5350;
        }
        
        html, body { width: 100%; height: 100%; margin: 0; padding: 0; overflow: hidden; background-color: var(--bg); color: var(--text); font-family: 'Inter', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; display: flex; }
        
        #editor-container { width: 70%; height: 100%; display: flex; flex-direction: column; position: relative; }
        
        #editor-header {
            height: 40px;
            background: var(--elevated);
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 15px;
            box-sizing: border-box;
        }
        .header-title {
            font-size: 12px;
            font-weight: 600;
            color: var(--text2);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        #monaco-root { flex: 1; width: 100%; }
        
        #resizer { width: 4px; height: 100%; background: var(--bg); cursor: col-resize; z-index: 10; transition: background 0.2s; border-right: 1px solid var(--border); border-left: 1px solid var(--border); }
        #resizer:hover, #resizer.active { background: var(--accent); }
        
        #chat-container { flex: 1; display: flex; flex-direction: column; min-width: 250px; background: var(--surface); }
        
        #chat-history { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 20px; }
        .message { max-width: 92%; padding: 12px 16px; border-radius: 8px; line-height: 1.5; font-size: 13px; word-wrap: break-word; box-shadow: 0 2px 5px rgba(0,0,0,0.2); }
        .user-message { align-self: flex-end; background-color: var(--elevated); color: var(--text); border: 1px solid var(--border); border-bottom-right-radius: 2px; }
        .ai-message { align-self: flex-start; background-color: transparent; color: var(--text); width: 100%; box-shadow: none; padding: 0; }
        
        .ai-message pre { background-color: var(--bg); padding: 0; border-radius: 6px; overflow: hidden; margin: 12px 0; border: 1px solid var(--border); box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
        .ai-message pre code { display: block; padding: 15px; overflow-x: auto; font-family: 'Roboto Mono', Consolas, monospace; font-size: 12px; }
        
        .code-header { display: flex; justify-content: space-between; align-items: center; background: var(--elevated); padding: 6px 12px; border-bottom: 1px solid var(--border); font-family: sans-serif; font-size: 11px; color: var(--text2); }
        
        .btn { background: var(--elevated); color: var(--text); border: 1px solid var(--border); padding: 6px 12px; border-radius: 4px; cursor: pointer; transition: 0.2s; font-size: 12px; font-weight: 500; display: flex; align-items: center; gap: 6px; }
        .btn:hover { background: var(--border); color: #fff; border-color: var(--accent-light); }
        .apply-btn { padding: 4px 10px; font-size: 11px; }
        
        #input-area { padding: 20px; background: var(--surface); display: flex; flex-direction: column; gap: 8px; }
        .input-wrapper { display: flex; align-items: flex-end; background: var(--elevated); border: 1px solid var(--border); border-radius: 8px; padding: 8px; transition: border-color 0.2s; box-shadow: 0 4px 12px rgba(0,0,0,0.2); position: relative; }
        .input-wrapper:focus-within { border-color: var(--accent); }
        .attach-wrap { position: relative; margin-bottom: 4px; }
        #attach-btn { background: transparent; color: var(--text2); border: 1px solid var(--border); width: 32px; height: 32px; border-radius: 6px; cursor: pointer; display: flex; justify-content: center; align-items: center; margin-right: 8px; transition: 0.2s; font-size: 18px; line-height: 1; }
        #attach-btn:hover { color: #fff; border-color: var(--accent-light); background: var(--border); }
        #attach-menu { position: absolute; left: 0; bottom: 38px; min-width: 180px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.35); padding: 6px; display: none; z-index: 20; }
        #attach-menu.open { display: block; }
        .attach-item { width: 100%; background: transparent; color: var(--text); border: none; border-radius: 6px; padding: 8px 10px; text-align: left; cursor: pointer; font-size: 12px; }
        .attach-item:hover { background: var(--elevated); }
        #attachments-list { display: flex; flex-wrap: wrap; gap: 6px; }
        #input-tools { display: flex; justify-content: flex-end; align-items: center; }
        #apply-changes-btn { background: var(--elevated); color: var(--text2); border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: 12px; cursor: pointer; transition: 0.2s; }
        #apply-changes-btn:hover { color: #fff; border-color: var(--accent-light); }
        #apply-changes-btn:disabled { opacity: 0.45; cursor: not-allowed; }
        .attachment-chip { border: 1px solid var(--border); background: var(--elevated); color: var(--text2); border-radius: 999px; padding: 4px 8px; font-size: 11px; display: inline-flex; align-items: center; gap: 6px; }
        .attachment-chip button { border: none; background: transparent; color: var(--text2); cursor: pointer; font-size: 11px; }
        .attachment-chip button:hover { color: var(--red); }
        .ai-line-preview { background: rgba(41, 98, 255, 0.16); border-left: 2px solid rgba(92, 138, 255, 0.95); }
        .ai-line-preview-glyph { border-left: 3px solid rgba(92, 138, 255, 0.95); }
        
        #chat-input { flex: 1; background: transparent; color: var(--text); border: none; padding: 8px; resize: none; font-family: inherit; font-size: 13px; min-height: 24px; max-height: 150px; outline: none; line-height: 1.5; }
        
        #action-btn { background: var(--accent); color: white; border: none; width: 32px; height: 32px; border-radius: 6px; cursor: pointer; display: flex; justify-content: center; align-items: center; margin-bottom: 4px; margin-left: 8px; transition: 0.2s; }
        #action-btn:hover { background: var(--accent-light); }
        #action-btn.stop-mode { background: var(--elevated); border: 1px solid var(--red); color: var(--red); }
        #action-btn.stop-mode:hover { background: rgba(239, 83, 80, 0.1); }
        
        .thinking { font-style: italic; color: var(--text2); animation: pulse 1.5s infinite; }
        @keyframes pulse { 0% { opacity: 0.6; } 50% { opacity: 1; } 100% { opacity: 0.6; } }

        /* Markdown text styling */
        .ai-message p { margin-top: 0; margin-bottom: 12px; }
        .ai-message p:last-child { margin-bottom: 0; }
        .ai-message strong { color: #fff; font-weight: 600; }
        .ai-message code:not(.hljs) { background: var(--elevated); padding: 2px 5px; border-radius: 4px; font-family: 'Roboto Mono', Consolas, monospace; font-size: 12px; color: var(--accent-light); border: 1px solid var(--border); }
        
        /* Scrollbars */
        ::-webkit-scrollbar { width: 8px; height: 8px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: var(--text3); }
    </style>
    
    <link rel="stylesheet" data-name="vs/editor/editor.main" href="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs/editor/editor.main.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">
</head>
<body>
    <div id="editor-container">
        <div id="editor-header">
            <span class="header-title">Strategy Editor</span>
            <button class="btn" onclick="saveStrategy()">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M17 3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V7l-4-4zm-5 16c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3zm3-10H5V5h10v4z"/></svg>
                Save Strategy
            </button>
        </div>
        <div id="monaco-root"></div>
    </div>
    
    <div id="resizer"></div>
    
    <div id="chat-container">
        <div id="chat-history"></div>
        <div id="input-area">
            <div id="input-tools">
                <button id="apply-changes-btn" onclick="applyPendingChanges()" disabled>Apply Changes</button>
            </div>
            <div id="attachments-list"></div>
            <div class="input-wrapper">
                <div class="attach-wrap">
                    <button id="attach-btn" title="Attach">+</button>
                    <div id="attach-menu">
                        <button class="attach-item" onclick="requestAttachment('image')">🖼 Photo</button>
                        <button class="attach-item" onclick="requestAttachment('document')">📄 Document</button>
                        <button class="attach-item" onclick="requestAttachment('chart')">📐 Choose from chart</button>
                    </div>
                </div>
                <textarea id="chat-input" placeholder="Ask AI to generate or fix a strategy... (Shift+Enter for new line)"></textarea>
                <button id="action-btn" onclick="handleAction()">
                    <svg id="icon-send" viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"></path></svg>
                    <svg id="icon-stop" viewBox="0 0 24 24" width="18" height="18" fill="currentColor" style="display: none;"><rect x="6" y="6" width="12" height="12"></rect></svg>
                </button>
            </div>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/marked/11.1.1/marked.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
    <script>var require = { paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs' } };</script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs/loader.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs/editor/editor.main.nls.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs/editor/editor.main.js"></script>
    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    
    <script>
        var editor;
        var pyBridge;
        var currentAiMessageDiv = null;
        var currentAiMarkdown = "";
        var isGenerating = false;
        var chartSelectionContext = null;
        var selectedAttachments = [];
        var pendingCodeCandidate = "";
        var previewBaselineCode = "";
        var previewDecorations = [];

        const inputEl = document.getElementById('chat-input');
        const attachBtn = document.getElementById('attach-btn');
        const attachMenu = document.getElementById('attach-menu');
        inputEl.addEventListener('input', function() {
            this.style.height = '24px';
            this.style.height = (this.scrollHeight) + 'px';
        });
        attachBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            attachMenu.classList.toggle('open');
        });
        window.addEventListener('click', function() {
            attachMenu.classList.remove('open');
        });

        marked.setOptions({
            highlight: function(code, lang) {
                if (lang && hljs.getLanguage(lang)) {
                    return hljs.highlight(code, { language: lang }).value;
                }
                return hljs.highlightAuto(code).value;
            }
        });

        const renderer = new marked.Renderer();
        renderer.code = function(code, lang) {
            const validLang = !!(lang && hljs.getLanguage(lang));
            const highlighted = validLang ? hljs.highlight(code, { language: lang }).value : hljs.highlightAuto(code).value;
            const escapedCode = code.replace(/'/g, "&#39;").replace(/"/g, "&quot;").replace(/\\/g, "\\\\");
            
            return `
                <div class="code-header">
                    <span>${lang || 'python'}</span>
                    <button class="btn apply-btn" onclick="applyCodeToEditor(this)">
                        <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>
                        Apply to Editor
                    </button>
                </div>
                <pre><code class="hljs ${lang}">${highlighted}</code><textarea style="display:none;" class="raw-code">${escapedCode}</textarea></pre>
            `;
        };
        marked.use({ renderer });

        const resizer = document.getElementById('resizer');
        const leftPanel = document.getElementById('editor-container');
        let isResizing = false;

        resizer.addEventListener('mousedown', (e) => {
            isResizing = true;
            resizer.classList.add('active');
            document.body.style.cursor = 'col-resize';
        });

        window.addEventListener('mousemove', (e) => {
            if (!isResizing) return;
            const newWidth = (e.clientX / window.innerWidth) * 100;
            if (newWidth > 10 && newWidth < 90) {
                leftPanel.style.width = newWidth + '%';
            }
        });

        window.addEventListener('mouseup', () => {
            if (isResizing) {
                isResizing = false;
                resizer.classList.remove('active');
                document.body.style.cursor = 'default';
                if(editor) editor.layout();
            }
        });
        
        window.addEventListener('resize', () => {
            if(editor) editor.layout();
        });

        inputEl.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleAction();
            }
        });

        require(['vs/editor/editor.main'], function() {
            editor = monaco.editor.create(document.getElementById('monaco-root'), {
                value: [
                    'from app import Strategy',
                    '',
                    'class AIStrategy(Strategy):',
                    '    name = "AI Strategy"',
                    '    ',
                    '    def on_candle(self, ctx):',
                    '        pass'
                ].join('\n'),
                language: 'python',
                theme: 'vs-dark',
                automaticLayout: true,
                minimap: { enabled: true },
                fontSize: 13,
                fontFamily: "'Roboto Mono', Consolas, monospace",
                scrollBeyondLastLine: false,
                padding: { top: 15 }
            });

            new QWebChannel(qt.webChannelTransport, function(channel) {
                pyBridge = channel.objects.bridge;
                pyBridge.editor_ready();
            });
        });

        function handleAction() {
            if (isGenerating) {
                stopAI();
            } else {
                sendMessage();
            }
        }

        function setGeneratingState(generating) {
            isGenerating = generating;
            const btn = document.getElementById('action-btn');
            const iconSend = document.getElementById('icon-send');
            const iconStop = document.getElementById('icon-stop');
            
            if (generating) {
                btn.classList.add('stop-mode');
                iconSend.style.display = 'none';
                iconStop.style.display = 'block';
            } else {
                btn.classList.remove('stop-mode');
                iconSend.style.display = 'block';
                iconStop.style.display = 'none';
            }
        }

        function sendMessage() {
            const text = inputEl.value.trim();
            if(!text || !pyBridge) return;
            if (editor) {
                previewBaselineCode = editor.getValue();
            }
            pendingCodeCandidate = "";
            setApplyButtonEnabled(false);
            
            inputEl.value = "";
            inputEl.style.height = '24px';
            setGeneratingState(true);
            
            const history = document.getElementById('chat-history');
            history.innerHTML += `<div class="message user-message">${text.replace(/\n/g, "<br>")}</div>`;
            
            currentAiMessageDiv = document.createElement('div');
            currentAiMessageDiv.className = 'message ai-message';
            currentAiMessageDiv.innerHTML = '<span class="thinking">Thinking...</span>';
            history.appendChild(currentAiMessageDiv);
            
            currentAiMarkdown = "";
            history.scrollTop = history.scrollHeight;
            
            const code = editor ? editor.getValue() : "";
            const payloadObj = chartSelectionContext ? JSON.parse(JSON.stringify(chartSelectionContext)) : {};
            if (selectedAttachments.length > 0) {
                payloadObj.attachments = selectedAttachments;
            }
            const selectionPayload = Object.keys(payloadObj).length > 0 ? JSON.stringify(payloadObj) : "";
            pyBridge.receive_chat_request_ext(text, code, selectionPayload);
            selectedAttachments = [];
            renderAttachments();
        }

        function extractLatestCodeBlock(markdownText) {
            if (!markdownText) return "";
            const pythonBlocks = [...markdownText.matchAll(/```python\s*([\s\S]*?)```/gi)];
            if (pythonBlocks.length > 0) {
                return (pythonBlocks[pythonBlocks.length - 1][1] || "").trim();
            }
            const genericBlocks = [...markdownText.matchAll(/```[a-zA-Z0-9_+-]*\s*([\s\S]*?)```/g)];
            if (genericBlocks.length > 0) {
                return (genericBlocks[genericBlocks.length - 1][1] || "").trim();
            }
            return "";
        }

        function computeChangedLines(beforeText, afterText) {
            const a = (beforeText || "").split('\n');
            const b = (afterText || "").split('\n');
            const n = Math.max(a.length, b.length);
            const changed = [];
            for (let i = 0; i < n; i++) {
                if ((a[i] || "") !== (b[i] || "")) {
                    changed.push(i + 1);
                }
            }
            return changed;
        }

        function applyPreviewToEditor(codeText) {
            if (!editor || !codeText) return;
            const before = editor.getValue();
            if (before === codeText) return;
            editor.setValue(codeText);
            highlightChangedLines(previewBaselineCode || before, codeText);
        }

        function highlightChangedLines(beforeText, afterText) {
            if (!editor) return;
            const changed = computeChangedLines(beforeText, afterText);
            const decs = changed.map((line) => ({
                range: new monaco.Range(line, 1, line, 1),
                options: {
                    isWholeLine: true,
                    className: 'ai-line-preview',
                    linesDecorationsClassName: 'ai-line-preview-glyph'
                }
            }));
            previewDecorations = editor.deltaDecorations(previewDecorations, decs);
        }

        function setApplyButtonEnabled(enabled) {
            const btn = document.getElementById('apply-changes-btn');
            if (btn) {
                btn.disabled = !enabled;
            }
        }

        function applyPendingChanges() {
            if (!editor || !pendingCodeCandidate) return;
            applyPreviewToEditor(pendingCodeCandidate);
            previewBaselineCode = editor.getValue();
            appendSystemMessage("AI changes applied.");
        }

        function requestChartSelection() {
            if (pyBridge) {
                pyBridge.receive_chart_selection_request();
                appendSystemMessage("Selection mode enabled. Drag on chart to capture area context.");
            }
        }

        function requestAttachment(kind) {
            attachMenu.classList.remove('open');
            if (!pyBridge) return;
            if (kind === 'chart') {
                requestChartSelection();
                return;
            }
            pyBridge.receive_attachment_request(kind);
        }

        function renderAttachments() {
            const host = document.getElementById('attachments-list');
            if (!host) return;
            if (!selectedAttachments.length) {
                host.innerHTML = '';
                return;
            }
            host.innerHTML = selectedAttachments.map((a, idx) => {
                const label = `${a.kind === 'image' ? '🖼' : '📄'} ${a.name}`;
                return `<span class="attachment-chip">${label}<button onclick="removeAttachment(${idx})">✕</button></span>`;
            }).join('');
        }

        function removeAttachment(idx) {
            selectedAttachments.splice(idx, 1);
            renderAttachments();
        }

        function set_attachment_from_python(payload) {
            if (!payload || !payload.path) return;
            selectedAttachments.push(payload);
            renderAttachments();
            appendSystemMessage(`Attached: ${payload.name}`);
        }

        function stopAI() {
            if(pyBridge) {
                pyBridge.receive_stop_ai();
            }
        }

        function saveStrategy() {
            if(pyBridge && editor) {
                pyBridge.receive_save_strategy(editor.getValue());
            }
        }

        function applyCodeToEditor(btn) {
            const rawCodeEl = btn.parentElement.nextElementSibling.querySelector('.raw-code');
            if(rawCodeEl && editor) {
                let code = rawCodeEl.value;
                const txt = document.createElement("textarea");
                txt.innerHTML = code;
                code = txt.value;
                editor.setValue(code);
                appendSystemMessage("Code applied to editor.");
            }
        }

        function stream_ai_chunk(chunk) {
            if(currentAiMessageDiv) {
                if(currentAiMarkdown === "") {
                    currentAiMessageDiv.innerHTML = "";
                }
                currentAiMarkdown += chunk;
                currentAiMessageDiv.innerHTML = marked.parse(currentAiMarkdown);
                const latestCode = extractLatestCodeBlock(currentAiMarkdown);
                if (latestCode) {
                    pendingCodeCandidate = latestCode;
                    setApplyButtonEnabled(true);
                    applyPreviewToEditor(latestCode);
                }
                
                const history = document.getElementById('chat-history');
                history.scrollTop = history.scrollHeight;
            }
        }

        function finish_ai_message() {
            currentAiMessageDiv = null;
            currentAiMarkdown = "";
            setGeneratingState(false);
            setApplyButtonEnabled(!!pendingCodeCandidate);
            inputEl.focus();
        }

        function appendSystemMessage(msg, isError=false) {
            const history = document.getElementById('chat-history');
            const color = isError ? 'var(--red)' : '#4CAF50';
            history.innerHTML += `<div class="message" style="background-color: transparent; border: 1px solid ${color}; color: ${color}; align-self: center; font-size: 11px; padding: 4px 10px; opacity: 0.8;">${msg}</div>`;
            history.scrollTop = history.scrollHeight;
        }

        function set_chart_selection_context(payload) {
            chartSelectionContext = payload;
            appendSystemMessage("Chart area captured and attached to AI context.");
        }

        function clear_chart_selection_context() {
            chartSelectionContext = null;
            appendSystemMessage("Chart area context cleared.");
        }

        function set_chat_input_text(text) {
            inputEl.value = text || "";
            inputEl.dispatchEvent(new Event('input'));
            inputEl.focus();
        }

        function set_chat_only_mode(enabled) {
            const editorContainer = document.getElementById('editor-container');
            const resizerEl = document.getElementById('resizer');
            const chatContainer = document.getElementById('chat-container');
            if (enabled) {
                editorContainer.style.display = 'none';
                resizerEl.style.display = 'none';
                chatContainer.style.width = '100%';
            } else {
                editorContainer.style.display = 'flex';
                resizerEl.style.display = 'block';
                chatContainer.style.width = 'auto';
            }
        }

        function on_ai_error(msg) {
            appendSystemMessage("Error: " + msg, true);
            finish_ai_message();
        }

        function set_editor_value(text) {
            if(editor) {
                editor.setValue(text);
            }
        }
        
        function append_ai_error_ask(traceback) {
             const history = document.getElementById('chat-history');
             history.innerHTML += `<div class="message user-message">Fix this error:<br><pre style="background:var(--bg);padding:8px;border-radius:4px;overflow-x:auto;">${traceback}</pre></div>`;
             
             currentAiMessageDiv = document.createElement('div');
             currentAiMessageDiv.className = 'message ai-message';
             currentAiMessageDiv.innerHTML = '<span class="thinking">Thinking...</span>';
             history.appendChild(currentAiMessageDiv);
             currentAiMarkdown = "";
             history.scrollTop = history.scrollHeight;
             setGeneratingState(true);
            pendingCodeCandidate = "";
            setApplyButtonEnabled(false);
        }
    </script>
</body>
</html>
"""

class UnifiedBridge(QObject):
    ready = Signal()
    chat_requested = Signal(str, str) # prompt, code_context
    chat_requested_ext = Signal(str, str, str) # prompt, code_context, selection_context_json
    run_backtest_requested = Signal(str) # code (unused now)
    stop_ai_requested = Signal()
    save_strategy_requested = Signal(str)
    chart_selection_requested = Signal()
    attachment_requested = Signal(str)

    @Slot()
    def editor_ready(self):
        self.ready.emit()

    @Slot(str, str)
    def receive_chat_request(self, prompt, code_context):
        self.chat_requested.emit(prompt, code_context)
        self.chat_requested_ext.emit(prompt, code_context, "")

    @Slot(str, str, str)
    def receive_chat_request_ext(self, prompt, code_context, selection_context_json):
        self.chat_requested.emit(prompt, code_context)
        self.chat_requested_ext.emit(prompt, code_context, selection_context_json)
        
    @Slot(str)
    def receive_run_backtest(self, code):
        self.run_backtest_requested.emit(code)

    @Slot()
    def receive_stop_ai(self):
        self.stop_ai_requested.emit()

    @Slot(str)
    def receive_save_strategy(self, code):
        self.save_strategy_requested.emit(code)

    @Slot()
    def receive_chart_selection_request(self):
        self.chart_selection_requested.emit()

    @Slot(str)
    def receive_attachment_request(self, kind):
        self.attachment_requested.emit(kind)


class AIAssistantEditor(QWebEngineView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bridge = UnifiedBridge()
        self.channel = QWebChannel(self)
        self.channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(self.channel)
        
        # Load the HTML from string
        self.setHtml(WEB_APP_HTML)

    def stream_chunk(self, chunk):
        escaped = json.dumps(chunk)
        self.page().runJavaScript(f"stream_ai_chunk({escaped});")
        
    def finish_generation(self):
        self.page().runJavaScript("finish_ai_message();")
        
    def show_error(self, msg):
        escaped = json.dumps(msg)
        self.page().runJavaScript(f"on_ai_error({escaped});")
        
    def show_system_message(self, msg, is_error=False):
        escaped = json.dumps(msg)
        is_err_str = "true" if is_error else "false"
        self.page().runJavaScript(f"appendSystemMessage({escaped}, {is_err_str});")

    def ask_ai_to_fix(self, traceback_str):
        escaped = json.dumps(traceback_str)
        self.page().runJavaScript(f"append_ai_error_ask({escaped});")

    def set_chart_selection_context(self, context_payload):
        escaped = json.dumps(context_payload)
        self.page().runJavaScript(f"set_chart_selection_context({escaped});")

    def clear_chart_selection_context(self):
        self.page().runJavaScript("clear_chart_selection_context();")

    def set_chat_input_text(self, text):
        escaped = json.dumps(text)
        self.page().runJavaScript(f"set_chat_input_text({escaped});")

    def set_chat_only_mode(self, enabled=True):
        enabled_str = "true" if enabled else "false"
        self.page().runJavaScript(f"set_chat_only_mode({enabled_str});")

    def set_attachment(self, payload):
        escaped = json.dumps(payload)
        self.page().runJavaScript(f"set_attachment_from_python({escaped});")
