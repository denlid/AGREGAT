import logging
import os
import importlib.util
from PySide6.QtCore import Slot
from PySide6.QtWidgets import QWidget, QVBoxLayout

from gui.widgets.ai_editor_web import AIAssistantEditor
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
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Unified Editor + Chat WebEngineView
        self.editor = AIAssistantEditor(self)
        main_layout.addWidget(self.editor)
        
        # Connect signals from JS bridge
        self.editor.bridge.chat_requested.connect(self.on_chat_requested)
        self.editor.bridge.run_backtest_requested.connect(self.on_run_backtest)
        self.editor.bridge.stop_ai_requested.connect(self.on_stop_ai)
        self.editor.bridge.save_strategy_requested.connect(self.on_save_strategy)

    def on_chat_requested(self, prompt, code_context):
        # Disable inputs and show loading state is handled by JS
        provider = self.pw.settings_tab.ai_provider_cb.currentText()
        api_key = self.pw.settings_tab.ai_api_key_le.text().strip()

        self._start_ai_worker(provider, api_key, prompt, code_context, self.latest_error)

    def _start_ai_worker(self, provider, api_key, prompt, code_context, error_traceback):
        self.latest_error = None # reset
        self.ai_worker = AIAssistantWorker(provider, api_key, prompt, code_context, error_traceback)
        self.ai_worker.chunk_received.connect(self.editor.stream_chunk)
        self.ai_worker.error_occurred.connect(self.editor.show_error)
        self.ai_worker.finished_generation.connect(self._on_ai_finished)
        self.ai_worker.start()

    @Slot(str)
    def _on_ai_finished(self, full_text):
        self.editor.finish_generation()

    def on_run_backtest(self, code):
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
            try:
                self.pw.bar_replay.run_to_end()
            except AttributeError:
                self.pw._replay_ctrl.bar_replay.run_to_end()

            self.editor.show_system_message("Strategy compiled and backtest started. Check 'Chart' or 'Stats' tab.")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self.handle_backtest_error(tb)

    def handle_backtest_error(self, traceback_str):
        self.latest_error = traceback_str
        self.editor.ask_ai_to_fix(traceback_str)
        
        provider = self.pw.settings_tab.ai_provider_cb.currentText()
        api_key = self.pw.settings_tab.ai_api_key_le.text().strip()
        
        # We need the code context. Since we are in python, we don't have it directly.
        # But we saved it to TEMP_STRAT_PATH
        code_context = ""
        if os.path.exists(TEMP_STRAT_PATH):
            with open(TEMP_STRAT_PATH, "r", encoding="utf-8") as f:
                code_context = f.read()
                
        self._start_ai_worker(provider, api_key, "Fix this error.", code_context, self.latest_error)

    def on_stop_ai(self):
        if self.ai_worker:
            self.ai_worker.stop()

    def on_save_strategy(self, code):
        from PySide6.QtWidgets import QFileDialog
        from config import _APP_DIR
        
        start_path = os.path.join(_APP_DIR, "strategies")
        if not os.path.exists(start_path):
            os.makedirs(start_path)
            
        path, _ = QFileDialog.getSaveFileName(self, "Save Strategy", start_path, "Python Files (*.py)")
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(code)
                self.editor.show_system_message(f"Successfully saved to {os.path.basename(path)}")
            except Exception as e:
                self.editor.show_system_message(f"Save failed: {e}", True)
