import os
import importlib.util
import json
from PySide6.QtCore import Slot
from PySide6.QtWidgets import QWidget, QVBoxLayout, QFileDialog

from gui.widgets.ai_editor_web import AIAssistantEditor
from gui.workers.ai_worker import AIAssistantWorker

_APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMP_STRAT_PATH = os.path.join(_APP_DIR, "strategies", "temp_ai_strategy.py")

class StrategyEditorTab(QWidget):
    def __init__(self, engine, main_window):
        super().__init__()
        self.engine = engine
        self.pw = main_window
        self.ai_worker = None
        self.latest_error = None
        self._strategy_file_path: str | None = None
        self._queued_editor_source: str | None = None
        self._editor_web_ready = False
        self._editor_dirty = False
        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Unified Editor + Chat WebEngineView
        self.editor = AIAssistantEditor(self)
        main_layout.addWidget(self.editor)
        
        # Connect signals from JS bridge
        self.editor.bridge.chat_requested_ext.connect(self.on_chat_requested_ext)
        self.editor.bridge.run_backtest_requested.connect(self.on_run_backtest)
        self.editor.bridge.stop_ai_requested.connect(self.on_stop_ai)
        self.editor.bridge.save_strategy_requested.connect(self.on_save_strategy)
        self.editor.bridge.chart_selection_requested.connect(self.on_chart_selection_requested)
        self.editor.bridge.attachment_requested.connect(self.on_attachment_requested)
        self.editor.bridge.ready.connect(self._on_editor_web_ready)
        self.editor.bridge.editor_model_changed.connect(self._on_editor_model_changed)

    def _on_editor_model_changed(self) -> None:
        self._editor_dirty = True
        self._refresh_header_title()

    def should_sync_editor_to_engine(self) -> bool:
        return self._editor_dirty

    def _on_editor_web_ready(self) -> None:
        self._editor_web_ready = True
        if self._queued_editor_source is not None:
            self.editor.set_editor_value(self._queued_editor_source)
            self._queued_editor_source = None
        self._refresh_header_title()
        # Restore persisted chat tabs from disk into Editor panel
        self.pw._restore_chat_tabs_to_editor()

    def _header_base_label(self) -> str:
        if self._strategy_file_path:
            return os.path.basename(self._strategy_file_path)
        return "New strategy"

    def _refresh_header_title(self) -> None:
        base = self._header_base_label()
        self.editor.set_strategy_header_title(f"{base} *" if self._editor_dirty else base)

    def bind_strategy_file(self, fp: str) -> None:
        """Show ``fp`` in Monaco and bind Save / auto-sync to that path."""
        self._editor_dirty = False
        fp = os.path.normpath(os.path.abspath(fp))
        try:
            with open(fp, encoding="utf-8") as f:
                src = f.read()
        except OSError:
            return
        self._strategy_file_path = fp
        if self._editor_web_ready:
            self.editor.set_editor_value(src)
        else:
            self._queued_editor_source = src
        self._refresh_header_title()

    def sync_engine_from_editor(self) -> None:
        """When leaving Editor for Chart: persist source and ``load_strategy`` from Monaco.

        Does **not** call ``on_reset()`` — existing trades / equity / candle index stay;
        only **subsequent** stepping uses the new strategy class. See TODO below.
        """
        # TODO: Let the user choose between a full chart reload (``on_reset`` and replay to
        # ``current_idx``) vs continuing with the current simulation state and only applying
        # the edited strategy on subsequent candles (current behaviour).
        self.editor.page().runJavaScript(
            "(function(){ try { return editor.getValue(); } catch(e) { return ''; } })()",
            self._on_editor_text_to_apply_to_engine,
        )

    def _on_editor_text_to_apply_to_engine(self, code: object) -> None:
        if not isinstance(code, str) or not code.strip():
            return
        bound = self._strategy_file_path
        if bound:
            try:
                with open(bound, "w", encoding="utf-8") as f:
                    f.write(code)
            except OSError as e:
                self.editor.show_system_message(f"Auto-save failed: {e}", True)
        try:
            self._apply_compiled_code_to_engine(code)
        except Exception as e:
            self.pw.show_error(f"Strategy compile failed:\n{e}")
            return
        self._editor_dirty = False
        self._refresh_header_title()

    def _apply_compiled_code_to_engine(self, code: str) -> None:
        instance = self._strategy_instance_from_code(code)
        self.engine.load_strategy(instance)
        if hasattr(self.pw.settings_tab, "rebuild_strategy_form"):
            try:
                self.pw.settings_tab.rebuild_strategy_form(instance)
            except Exception:
                pass
        try:
            self.engine.position_changed.emit()
        except Exception:
            pass
        try:
            self.engine.update_stats()
        except Exception:
            pass

    def _strategy_instance_from_code(self, code: str):
        os.makedirs(os.path.dirname(TEMP_STRAT_PATH), exist_ok=True)
        with open(TEMP_STRAT_PATH, "w", encoding="utf-8") as f:
            f.write(code)
        spec = importlib.util.spec_from_file_location("temp_ai_strategy", TEMP_STRAT_PATH)
        if spec is None or spec.loader is None:
            raise ValueError("Could not load strategy module.")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from engine.strategy.context import Strategy

        strategy_cls = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and issubclass(attr, Strategy) and attr.__name__ not in (
                "Strategy",
                "BaseStrategy",
            ):
                strategy_cls = attr
                break
        if strategy_cls is None:
            raise ValueError("Could not find a class inheriting from `Strategy` in the code.")
        return strategy_cls()

    def on_chat_requested_ext(self, prompt, code_context, selection_context_json):
        # Disable inputs and show loading state is handled by JS
        extra_context = {}
        if selection_context_json:
            try:
                extra_context = json.loads(selection_context_json)
            except Exception:
                extra_context = {}

        # Mirror user message to the twin panel
        self.editor.forward_user_message_to_mirror(prompt)

        self._start_ai_worker(
            prompt, code_context, self.latest_error, extra_context=extra_context
        )

    def _start_ai_worker(
        self, prompt, code_context, error_traceback, extra_context=None
    ):
        self.latest_error = None # reset
        kw = self.pw.settings_tab.get_ai_worker_kwargs()
        self.ai_worker = AIAssistantWorker(
            prompt=prompt,
            code_context=code_context,
            error_traceback=error_traceback,
            extra_context=extra_context or {},
            **kw,
        )
        self.ai_worker.chunk_received.connect(self.editor.stream_chunk)
        self.ai_worker.phase_changed.connect(self.editor.set_ai_phase)
        self.ai_worker.error_occurred.connect(self.editor.show_error)
        self.ai_worker.finished_generation.connect(self._on_ai_finished)
        self.ai_worker.start()

    @Slot(str)
    def _on_ai_finished(self, full_text):
        # JS parses streamed markdown; signal completion to re-enable UI.
        self.editor.finish_generation(full_text or "")

    def on_run_backtest(self, code):
        try:
            self._apply_compiled_code_to_engine(code)

            # 5. Run the backtest
            self.pw.on_reset()
            try:
                self.pw.bar_replay.run_to_end()
            except AttributeError:
                self.pw._replay_ctrl.bar_replay.run_to_end()

            self.editor.show_system_message("Strategy compiled and backtest started. Check 'Chart' or 'Stats' tab.")
            self._editor_dirty = False
            self._refresh_header_title()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self.handle_backtest_error(tb)

    def handle_backtest_error(self, traceback_str):
        self.latest_error = traceback_str
        self.editor.ask_ai_to_fix(traceback_str)
        
        # We need the code context. Since we are in python, we don't have it directly.
        # But we saved it to TEMP_STRAT_PATH
        code_context = ""
        if os.path.exists(TEMP_STRAT_PATH):
            with open(TEMP_STRAT_PATH, "r", encoding="utf-8") as f:
                code_context = f.read()

        self._start_ai_worker(
            "Fix this error.", code_context, self.latest_error, extra_context={}
        )

    def on_stop_ai(self):
        if self.ai_worker:
            self.ai_worker.stop()

    def on_save_strategy(self, code):
        bound = self._strategy_file_path
        if bound and os.path.isfile(bound):
            try:
                with open(bound, "w", encoding="utf-8") as f:
                    f.write(code)
            except OSError as e:
                self.editor.show_system_message(f"Save failed: {e}", True)
                return
            try:
                self._apply_compiled_code_to_engine(code)
            except Exception as e:
                self.pw.show_error(f"Saved file, but strategy did not load:\n{e}")
            self.editor.show_system_message(f"Saved {os.path.basename(bound)}")
            self._editor_dirty = False
            self._refresh_header_title()
            return

        start_path = os.path.join(_APP_DIR, "strategies")
        if not os.path.exists(start_path):
            os.makedirs(start_path)

        path, _ = QFileDialog.getSaveFileName(self, "Save Strategy", start_path, "Python Files (*.py)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(code)
                self._strategy_file_path = os.path.normpath(os.path.abspath(path))
                self.pw._settings_ctrl.save("last_strat", self._strategy_file_path)
            except OSError as e:
                self.editor.show_system_message(f"Save failed: {e}", True)
                return
            try:
                self._apply_compiled_code_to_engine(code)
            except Exception as e:
                self.pw.show_error(f"Saved file, but strategy did not load:\n{e}")
            self.editor.show_system_message(f"Saved to {os.path.basename(path)}")
            self._editor_dirty = False
            self._refresh_header_title()

    def on_chart_selection_requested(self):
        if hasattr(self.pw, "_on_chart_selection_requested"):
            self.pw._on_chart_selection_requested()

    def on_attachment_requested(self, kind):
        if kind == "image":
            flt = "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"
            title = "Select image"
        else:
            flt = "Documents (*.pdf *.txt *.md *.csv *.json *.doc *.docx);;All files (*.*)"
            title = "Select document"
        path, _ = QFileDialog.getOpenFileName(self, title, "", flt)
        if not path:
            return
        payload = {"kind": kind, "name": os.path.basename(path), "path": path}
        self.editor.set_attachment(payload)
