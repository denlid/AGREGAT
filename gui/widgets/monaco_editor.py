import logging
from PySide6.QtCore import QObject, Signal, Slot, Qt
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

logger = logging.getLogger("StrategyTester.gui.widgets.monaco_editor")

MONACO_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta http-equiv="Content-Type" content="text/html;charset=utf-8" />
    <style>
        html, body { width: 100%; height: 100%; margin: 0; padding: 0; overflow: hidden; background-color: #1e1e1e; }
        #container { width: 100%; height: 100%; }
    </style>
    <!-- Use CDN for Monaco Editor -->
    <link rel="stylesheet" data-name="vs/editor/editor.main" href="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs/editor/editor.main.css">
</head>
<body>
    <div id="container"></div>
    <script>var require = { paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs' } };</script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs/loader.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs/editor/editor.main.nls.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs/editor/editor.main.js"></script>
    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    
    <script>
        var editor;
        var pyBridge;

        require(['vs/editor/editor.main'], function() {
            editor = monaco.editor.create(document.getElementById('container'), {
                value: [
                    'from app import Strategy',
                    '',
                    'class AIStrategy(Strategy):',
                    '    name = "AI Strategy"',
                    '    ',
                    '    def on_candle(self, ctx):',
                    '        pass'
                ].join('\\n'),
                language: 'python',
                theme: 'vs-dark',
                automaticLayout: true,
                minimap: { enabled: true },
                fontSize: 14,
                fontFamily: "Consolas, 'Courier New', monospace"
            });

            new QWebChannel(qt.webChannelTransport, function(channel) {
                pyBridge = channel.objects.bridge;
                pyBridge.editor_ready();
            });
        });

        // Methods called from Python
        function get_value() {
            if(pyBridge) {
                pyBridge.receive_text(editor.getValue());
            }
        }
        function set_value(text) {
            if(editor) {
                editor.setValue(text);
            }
        }
    </script>
</body>
</html>
"""

class MonacoBridge(QObject):
    ready = Signal()
    text_received = Signal(str)

    @Slot()
    def editor_ready(self):
        self.ready.emit()

    @Slot(str)
    def receive_text(self, text):
        self.text_received.emit(text)


class MonacoEditor(QWebEngineView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bridge = MonacoBridge()
        self.channel = QWebChannel(self)
        self.channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(self.channel)
        
        # Load the HTML from string
        self.setHtml(MONACO_HTML)
        
    def get_text(self, callback):
        """Asynchronously requests text from the editor.
        The callback will be fired with the text as its only argument.
        """
        self.bridge.text_received.connect(callback, type=Qt.ConnectionType.SingleShotConnection)
        self.page().runJavaScript("get_value();")

    def set_text(self, text):
        """Sets the editor content."""
        # Escape backticks and backslashes for JS injection
        escaped_text = text.replace('\\', '\\\\').replace('`', '\\`')
        self.page().runJavaScript(f"set_value(`{escaped_text}`);")
