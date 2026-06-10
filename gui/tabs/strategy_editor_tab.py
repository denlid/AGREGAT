import ast
import os
import importlib.util
import json
import hashlib
from PySide6.QtCore import Slot
from PySide6.QtWidgets import QWidget, QVBoxLayout, QFileDialog

from gui.widgets.ai_editor_web import AIAssistantEditor
from gui.workers.ai_worker import AIAssistantWorker

_APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMP_STRAT_PATH = os.path.join(_APP_DIR, "strategies", "temp_ai_strategy.py")


def _validate_code_syntax(code: str) -> tuple[bool, str]:
    """Check Python code for syntax errors. Returns (is_valid, error_message)."""
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        line_info = f"line {e.lineno}" if e.lineno else "unknown line"
        text_info = f"\n{e.text}" if e.text else ""
        return False, f"Syntax error at {line_info}: {e.msg}{text_info}"


def _syntax_diagnostics_for_code(code: str) -> list[dict]:
    """Return Monaco-compatible diagnostics for Python syntax errors."""
    if not code.strip():
        return []
    try:
        ast.parse(code)
    except SyntaxError as e:
        line = max(int(e.lineno or 1), 1)
        column = max(int(e.offset or 1), 1)
        end_line = max(int(getattr(e, "end_lineno", None) or line), line)
        end_column = int(getattr(e, "end_offset", None) or column + 1)
        if end_line == line and end_column <= column:
            end_column = column + 1
        return [
            {
                "message": e.msg,
                "startLineNumber": line,
                "startColumn": column,
                "endLineNumber": end_line,
                "endColumn": max(end_column, 1),
                "severity": "error",
            }
        ]
    return []


def _validate_strategy_structure(code: str) -> list[str]:
    """Return warnings about potential structural issues in strategy code."""
    warnings = []
    has_on_candle = "on_candle" in code
    has_vectorized = "vectorized_signals" in code
    has_api_strategy = "Strategy(" in code and ".on_candle" in code
    has_class = "class " in code

    if not has_class and not has_api_strategy:
        warnings.append("No class definition or api.Strategy object found")
    if not has_on_candle and not has_vectorized and not has_api_strategy:
        warnings.append("No execution method (on_candle/vectorized_signals) found")
    if "import " not in code:
        warnings.append("No imports found — code may be incomplete")
    if "self.params" in code and "PARAMS" not in code:
        warnings.append("Code uses self.params but has no PARAMS definition")
    if "Strategy" not in code and not has_api_strategy:
        warnings.append("Code does not inherit/use Strategy class")
    return warnings


# Cache for identical AI requests (2.3)
_AI_CACHE: dict[str, str] = {}
_MAX_CACHE_SIZE = 50


def _make_cache_key(prompt: str, code_context: str, error_traceback: str | None, extra_context_json: str) -> str:
    raw = f"{prompt}||{code_context}||{error_traceback or ''}||{extra_context_json}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        self._last_metrics: dict | None = None  # (1.5) cache last backtest metrics
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
        self.editor.bridge.editor_code_changed.connect(self._on_editor_code_changed)
        self.editor.bridge.rename_strategy_requested.connect(self.on_rename_strategy)

    def _on_editor_model_changed(self) -> None:
        self._editor_dirty = True
        self._refresh_header_title()

    def _on_editor_code_changed(self, code: str) -> None:
        self.editor.set_editor_diagnostics(_syntax_diagnostics_for_code(code))

    def should_sync_editor_to_engine(self) -> bool:
        return self._editor_dirty

    def _on_editor_web_ready(self) -> None:
        self._editor_web_ready = True
        if self._queued_editor_source is not None:
            self.editor.set_editor_value(self._queued_editor_source)
            self.editor.set_editor_diagnostics(_syntax_diagnostics_for_code(self._queued_editor_source))
            self._queued_editor_source = None
        self._refresh_header_title()
        # Restore persisted chat tabs from disk into Editor panel
        self.pw._restore_chat_tabs_to_editor()

    def _header_base_label(self) -> str:
        if self._strategy_file_path:
            return os.path.basename(self._strategy_file_path)
        return "New strategy"

    def _refresh_header_title(self) -> None:
        if not self._editor_web_ready:
            return
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
            self.editor.set_editor_diagnostics(_syntax_diagnostics_for_code(src))
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

        # Try 1: engine.api.Strategy objects (new simplified API)
        try:
            from engine.api import Strategy as ApiStrategy
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, ApiStrategy):
                    strategy_cls = attr._build_class()
                    return strategy_cls()
        except ImportError:
            pass

        # Try 2: legacy class-based Strategy subclasses
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

    # ------------------------------------------------------------------
    # (1.4) Build data schema from loaded CSV
    # ------------------------------------------------------------------
    def _build_data_schema(self) -> dict | None:
        """Extract schema info from the currently loaded data."""
        df = getattr(self.engine, "df", None)
        if df is None:
            return None
        schema = {
            "columns": list(df.columns),
            "rows": len(df),
            "dtypes": {col: str(df[col].dtype) for col in df.columns},
        }
        # Add date range if time column exists
        for col in df.columns:
            if "time" in col.lower() or "date" in col.lower():
                try:
                    schema["date_range"] = {
                        "from": str(df[col].iloc[0]),
                        "to": str(df[col].iloc[-1]),
                    }
                except Exception:
                    pass
                break
        # Add basic statistics for OHLCV columns
        stats = {}
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                try:
                    stats[col] = {
                        "min": float(df[col].min()),
                        "max": float(df[col].max()),
                        "mean": float(df[col].mean()),
                    }
                except Exception:
                    pass
        if stats:
            schema["statistics"] = stats
        return schema

    # ------------------------------------------------------------------
    # (1.5) Collect backtest metrics
    # ------------------------------------------------------------------
    def _collect_backtest_metrics(self) -> dict | None:
        """Extract key metrics from the engine after a backtest."""
        try:
            stats = getattr(self.engine, "stats", None) or {}
            if not stats:
                return None
            metrics = {}
            for key, label in (
                ("sharpe", "sharpe_ratio"),
                ("sortino", "sortino_ratio"),
                ("total_return_pct", "total_return_pct"),
                ("max_drawdown_pct", "max_drawdown_pct"),
                ("win_rate", "win_rate_pct"),
                ("profit_factor", "profit_factor"),
                ("avg_trade_pnl", "avg_trade_pnl"),
                ("num_trades", "num_trades"),
                ("recovery_factor", "recovery_factor"),
                ("calmar_ratio", "calmar_ratio"),
            ):
                val = stats.get(key) or stats.get(label)
                if val is not None:
                    metrics[label] = round(float(val), 4) if isinstance(val, (int, float)) else val
            return metrics if metrics else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # (2.7) Pre-validate code
    # ------------------------------------------------------------------
    def _pre_validate_code(self, code: str) -> list[str]:
        """Run validation checks on code before sending to AI. Return warnings."""
        warnings = []
        is_valid, err = _validate_code_syntax(code)
        if not is_valid:
            warnings.append(f"⚠️ Syntax error: {err}")
        struct_warnings = _validate_strategy_structure(code)
        warnings.extend(struct_warnings)
        return warnings

    # ------------------------------------------------------------------
    # AI chat flow
    # ------------------------------------------------------------------
    def on_chat_requested_ext(self, prompt, code_context, selection_context_json):
        # Pre-validate code before sending to AI (2.7)
        if code_context:
            validation_warnings = self._pre_validate_code(code_context)
            for w in validation_warnings:
                self.editor.show_system_message(w, is_error=True)

        extra_context = {}
        if selection_context_json:
            try:
                extra_context = json.loads(selection_context_json)
            except Exception:
                extra_context = {}

        # (1.4) Attach data schema
        data_schema = self._build_data_schema()
        if data_schema:
            extra_context["data_schema"] = data_schema

        # (1.5) Attach last backtest metrics
        if self._last_metrics:
            extra_context["backtest_metrics"] = self._last_metrics

        # (2.3) Check cache
        cache_key = _make_cache_key(prompt, code_context, self.latest_error, selection_context_json)
        cached = _AI_CACHE.get(cache_key)
        if cached:
            self.editor.show_system_message("⚡ Using cached response (same prompt was sent before)")
            self.editor.stream_chunk(cached)
            self.editor.finish_generation(cached)
            return

        # Mirror user message to the twin panel
        self.editor.forward_user_message_to_mirror(prompt)

        self._start_ai_worker(
            prompt, code_context, self.latest_error, extra_context=extra_context, cache_key=cache_key
        )

    def _start_ai_worker(
        self, prompt, code_context, error_traceback, extra_context=None, cache_key: str | None = None
    ):
        self.latest_error = None  # reset
        kw = self.pw.settings_tab.get_ai_worker_kwargs()
        
        # Store cache_key for later use in _on_ai_finished
        self._pending_cache_key = cache_key
        
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
        # (2.3) Cache the response
        cache_key = getattr(self, '_pending_cache_key', None)
        if cache_key and full_text:
            _AI_CACHE[cache_key] = full_text
            if len(_AI_CACHE) > _MAX_CACHE_SIZE:
                # Remove oldest entry
                oldest = next(iter(_AI_CACHE))
                del _AI_CACHE[oldest]
        
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

            # (1.5) Collect metrics after backtest
            self._last_metrics = self._collect_backtest_metrics()
            if self._last_metrics:
                summary = ", ".join(f"{k}={v}" for k, v in list(self._last_metrics.items())[:5])
                self.editor.show_system_message(f"📊 Backtest complete: {summary}")
            else:
                self.editor.show_system_message("Strategy compiled and backtest started. Check 'Chart' or 'Stats' tab.")
            
            self._editor_dirty = False
            self._refresh_header_title()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self.handle_backtest_error(tb)

    def handle_backtest_error(self, traceback_str):
        """Show error in the editor. Does NOT auto-trigger AI."""
        self.latest_error = traceback_str
        self.editor.show_system_message(f"⚠️ Backtest error:\n{traceback_str}", is_error=True)

    def on_stop_ai(self):
        if self.ai_worker:
            self.ai_worker.stop()
        # Signal the JS that generation was stopped by user (not an error)
        self.editor.finish_generation("__stopped__")

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

    def on_rename_strategy(self, new_name: str):
        """Rename the currently bound strategy file."""
        # Check if a file is bound
        if not self._strategy_file_path or not os.path.isfile(self._strategy_file_path):
            self.editor.show_rename_error("No strategy file is currently open")
            return

        # Validate new name
        new_name = new_name.strip()
        if not new_name:
            self.editor.show_rename_error("File name cannot be empty")
            return

        # Check for invalid characters
        invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
        for char in invalid_chars:
            if char in new_name:
                self.editor.show_rename_error(f"File name cannot contain: {' '.join(invalid_chars)}")
                return

        # Ensure .py extension
        if not new_name.endswith('.py'):
            new_name += '.py'

        # Build new path
        old_path = self._strategy_file_path
        directory = os.path.dirname(old_path)
        new_path = os.path.join(directory, new_name)
        new_path = os.path.normpath(os.path.abspath(new_path))

        # Check if already same name
        if old_path == new_path:
            return

        # Check if target file already exists
        if os.path.exists(new_path):
            self.editor.show_rename_error(f"File '{new_name}' already exists")
            return

        # Attempt rename
        try:
            os.rename(old_path, new_path)
            self._strategy_file_path = new_path
            # Update last_strat in settings
            self.pw._settings_ctrl.save("last_strat", new_path)
            # Update header title
            self._refresh_header_title()
            self.editor.show_system_message(f"Renamed to {new_name}")
        except OSError as e:
            self.editor.show_rename_error(f"Rename failed: {e}")
