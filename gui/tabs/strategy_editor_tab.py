import logging
import os
import importlib.util
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, 
    QPushButton, QTextBrowser, QPlainTextEdit, QMessageBox
)

from gui.widgets.monaco_editor import MonacoEditor
from gui.workers.ai_worker import AIAssistantWorker

logger = logging.getLogger("StrategyTester.gui.tabs.strategy_editor_tab")
_APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMP_STRAT_PATH = os.path.join(_APP_DIR, "strategies", "temp_ai_strategy.py")

class StrategyEditorTab(QWidget):
    def __init__(self, engine, main_window):
        super().__init__()
        self.engine = engine
        self.pw = main_window
        self.ai_worker = None
        self.latest_error = None
        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Toolbar
        toolbar = QHBoxLayout()
        self.btn_run = QPushButton("▶ Run Backtest")
        self.btn_run.clicked.connect(self.on_run_backtest)
        toolbar.addWidget(self.btn_run)
        
        self.btn_apply = QPushButton("📥 Apply Code from Chat")
        self.btn_apply.clicked.connect(self.apply_code_from_chat)
        toolbar.addWidget(self.btn_apply)
        
        toolbar.addStretch()
        main_layout.addLayout(toolbar)

        # Splitter
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.splitter, stretch=1)

        # Editor Pane
        self.editor = MonacoEditor(self)
        self.splitter.addWidget(self.editor)

        # Chat Pane
        chat_widget = QWidget()
        chat_layout = QVBoxLayout(chat_widget)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        
        self.chat_history = QTextBrowser()
        self.chat_history.setOpenExternalLinks(True)
        chat_layout.addWidget(self.chat_history, stretch=1)

        input_layout = QHBoxLayout()
        self.chat_input = QPlainTextEdit()
        self.chat_input.setMaximumHeight(80)
        self.chat_input.setPlaceholderText("Ask AI to generate or fix a strategy...")
        input_layout.addWidget(self.chat_input)

        self.btn_send = QPushButton("Send")
        self.btn_send.setMinimumHeight(80)
        self.btn_send.clicked.connect(self.on_send_chat)
        input_layout.addWidget(self.btn_send)

        chat_layout.addLayout(input_layout)
        self.splitter.addWidget(chat_widget)
        
        # Set splitter sizes (70% editor, 30% chat)
        self.splitter.setSizes([700, 300])

    def on_send_chat(self):
        prompt = self.chat_input.toPlainText().strip()
        if not prompt:
            return

        # Disable input while generating
        self.btn_send.setEnabled(False)
        self.chat_input.clear()
        
        self.chat_history.append(f"<b>You:</b> {prompt}<br>")
        self.chat_history.append("<b>AI:</b> <i>Thinking...</i>")

        # Get settings directly from UI to avoid stale dictionary
        provider = self.pw.settings_tab.ai_provider_cb.currentText()
        api_key = self.pw.settings_tab.ai_api_key_le.text().strip()

        # Get text from editor asynchronously
        self.editor.get_text(lambda text: self._start_ai_worker(provider, api_key, prompt, text, self.latest_error))

    def _start_ai_worker(self, provider, api_key, prompt, code_context, error_traceback):
        self.latest_error = None # reset
        self.ai_worker = AIAssistantWorker(provider, api_key, prompt, code_context, error_traceback)
        self.ai_worker.chunk_received.connect(self.on_ai_chunk)
        self.ai_worker.error_occurred.connect(self.on_ai_error)
        self.ai_worker.finished_generation.connect(self.on_ai_finished)
        self.ai_worker.start()

    @Slot(str)
    def on_ai_chunk(self, chunk):
        # Extremely basic streaming into QTextBrowser: 
        # Move cursor to end, insert text.
        cursor = self.chat_history.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.chat_history.setTextCursor(cursor)
        
        # If it's the first chunk, remove the "Thinking..."
        doc_text = self.chat_history.toHtml()
        if "<i>Thinking...</i>" in doc_text:
            new_html = doc_text.replace("<i>Thinking...</i>", "")
            self.chat_history.setHtml(new_html)
            
        self.chat_history.insertPlainText(chunk)
        self.chat_history.verticalScrollBar().setValue(self.chat_history.verticalScrollBar().maximum())

    @Slot(str)
    def on_ai_error(self, err_msg):
        doc_text = self.chat_history.toHtml()
        if "<i>Thinking...</i>" in doc_text:
            new_html = doc_text.replace("<i>Thinking...</i>", "")
            self.chat_history.setHtml(new_html)
            
        self.chat_history.append(f"<br><b style='color:red;'>Error:</b> {err_msg}<br>")
        self.chat_history.verticalScrollBar().setValue(self.chat_history.verticalScrollBar().maximum())
        self.btn_send.setEnabled(True)

    @Slot(str)
    def on_ai_finished(self, full_text):
        self.chat_history.append("<br><hr>")
        self.btn_send.setEnabled(True)
        # We store the latest response for "Apply"
        self._last_ai_code = self._extract_code(full_text)

    def _extract_code(self, text):
        import re
        match = re.search(r'```python\s+(.*?)\s+```', text, re.DOTALL)
        if match:
            return match.group(1)
        return ""

    def apply_code_from_chat(self):
        if hasattr(self, '_last_ai_code') and self._last_ai_code:
            self.editor.set_text(self._last_ai_code)
            self.chat_history.append("<i>Code applied to editor.</i>")
        else:
            QMessageBox.information(self, "Info", "No Python code found in the latest AI response.")

    def on_run_backtest(self):
        self.editor.get_text(self._execute_strategy_code)

    def _execute_strategy_code(self, code):
        try:
            # 1. Save to file
            os.makedirs(os.path.dirname(TEMP_STRAT_PATH), exist_ok=True)
            with open(TEMP_STRAT_PATH, "w", encoding="utf-8") as f:
                f.write(code)

            # 2. Dynamic import
            spec = importlib.util.spec_from_file_location("temp_ai_strategy", TEMP_STRAT_PATH)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # 3. Find the strategy class
            import app
            from engine.strategy.context import Strategy
            
            strategy_cls = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                # Need to gracefully handle the base classes
                if isinstance(attr, type) and issubclass(attr, Strategy) and attr.__name__ not in ("Strategy", "BaseStrategy"):
                    strategy_cls = attr
                    break

            if strategy_cls is None:
                raise ValueError("Could not find a class inheriting from `Strategy` in the code.")

            # 4. Load into engine
            instance = strategy_cls()
            self.engine.load_strategy(instance)
            if hasattr(self.pw.settings_tab, "rebuild_strategy_form"):
                self.pw.settings_tab.rebuild_strategy_form(instance)
            
            # 5. Run the backtest
            self.pw.on_reset()
            # Try to trigger the "Run to End" from MainWindow
            try:
                self.pw.bar_replay.run_to_end()
            except AttributeError:
                # If bar_replay doesn't have run_to_end or isn't there, we'll try something else
                self.pw._replay_ctrl.bar_replay.run_to_end()

            self.chat_history.append("<i>Strategy compiled and backtest started. Check 'Chart' or 'Stats' tab.</i>")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self.handle_backtest_error(tb)

    def handle_backtest_error(self, traceback_str):
        self.latest_error = traceback_str
        self.chat_history.append(f"<br><b style='color:red;'>Backtest Error:</b><br><pre>{traceback_str}</pre>")
        self.chat_history.append("<b>System:</b> Asking AI for a fix...")
        
        provider = self.pw.settings_tab.ai_provider_cb.currentText()
        api_key = self.pw.settings_tab.ai_api_key_le.text().strip()
        
        self.editor.get_text(
            lambda text: self._start_ai_worker(provider, api_key, "Fix this error.", text, self.latest_error)
        )
