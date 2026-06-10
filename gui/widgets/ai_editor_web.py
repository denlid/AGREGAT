import json
import os
from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

from gui.styling.theme import get_theme, get_theme_name, register_theme_listener, unregister_theme_listener

_WEB_APP_HTML_NAME = "ai_editor_web_app.html"


def _load_web_app_html() -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _WEB_APP_HTML_NAME)
    with open(path, encoding="utf-8") as f:
        return f.read()


class UnifiedBridge(QObject):
    ready = Signal()
    chat_requested = Signal(str, str)  # prompt, code_context
    chat_requested_ext = Signal(str, str, str)  # prompt, code_context, selection_context_json
    run_backtest_requested = Signal(str)  # code (unused now)
    stop_ai_requested = Signal()
    save_strategy_requested = Signal(str)
    chart_selection_requested = Signal()
    attachment_requested = Signal(str)
    editor_model_changed = Signal()
    editor_code_changed = Signal(str)
    chat_tabs_state_changed = Signal(str)  # JSON state pushed from JS
    rename_strategy_requested = Signal(str)  # new_name

    @Slot()
    def receive_editor_dirty(self) -> None:
        self.editor_model_changed.emit()

    @Slot(str)
    def receive_editor_code_changed(self, code: str) -> None:
        self.editor_code_changed.emit(code)

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

    @Slot(str)
    def receive_chat_tabs_state(self, state_json: str) -> None:
        """Called from JS when tabs state changes — forward to Python for cross-panel sync."""
        self.chat_tabs_state_changed.emit(state_json)

    @Slot(str)
    def receive_rename_strategy(self, new_name: str) -> None:
        """Called from JS when user wants to rename the strategy file."""
        self.rename_strategy_requested.emit(new_name)


class AIAssistantEditor(QWebEngineView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bridge = UnifiedBridge()
        self.channel = QWebChannel(self)
        self.channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(self.channel)
        self._mirror_panel: 'AIAssistantEditor | None' = None

        # Load bundled HTML next to this module
        self.setHtml(_load_web_app_html())
        self.bridge.ready.connect(self.apply_theme_to_web)
        register_theme_listener(self.apply_theme_to_web)
        self._theme_registered = True

    def closeEvent(self, event):
        self._teardown_theme()
        super().closeEvent(event)

    def destroy(self, destroyWindow=True, destroySubWindows=True):
        self._teardown_theme()
        super().destroy(destroyWindow, destroySubWindows)

    def _teardown_theme(self) -> None:
        if getattr(self, "_theme_registered", False):
            unregister_theme_listener(self.apply_theme_to_web)
            self._theme_registered = False

    def apply_theme_to_web(self, *_args) -> None:
        t = get_theme()
        payload = {
            "bg": t.get("bg"),
            "surface": t.get("surface"),
            "elevated": t.get("elevated"),
            "border": t.get("border2") or t.get("border"),
            "accent": t.get("accent"),
            "accentLight": t.get("accent_light"),
            "text": t.get("text"),
            "text2": t.get("text2"),
            "text3": t.get("text3"),
            "red": t.get("red"),
            "green": t.get("green"),
            "monacoTheme": "vs-light" if get_theme_name() == "light" else "vs-dark",
        }
        escaped = json.dumps(payload)
        self.page().runJavaScript(f"window.apply_app_theme && window.apply_app_theme({escaped});")

    def set_mirror_panel(self, panel: 'AIAssistantEditor | None') -> None:
        """Set a twin panel to mirror all chat messages to."""
        self._mirror_panel = panel

    def stream_chunk(self, chunk):
        escaped = json.dumps(chunk)
        self.page().runJavaScript(f"stream_ai_chunk({escaped});")

    def set_ai_phase(self, text: str) -> None:
        escaped = json.dumps(text)
        self.page().runJavaScript(f"setAiPhaseMessage({escaped});")

    def finish_generation(self, full_text: str = "") -> None:
        # If streaming produced 0 chunks, still render + run preview logic.
        escaped = json.dumps(full_text or "")
        self.page().runJavaScript(f"finish_ai_message({escaped});")
        # Forward user + AI pair to mirror panel as complete messages
        if self._mirror_panel and full_text:
            self._mirror_panel.page().runJavaScript(f"appendAiMessage({escaped});")

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

    def forward_user_message_to_mirror(self, prompt: str) -> None:
        """Forward user message text to the mirror panel as a user bubble."""
        if self._mirror_panel:
            escaped = json.dumps(prompt)
            self._mirror_panel.page().runJavaScript(f"appendUserMessage({escaped});")

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

    def set_editor_value(self, text: str) -> None:
        escaped = json.dumps(text or "")
        self.page().runJavaScript(f"window.set_editor_value({escaped});")

    def set_editor_diagnostics(self, diagnostics: list[dict]) -> None:
        escaped = json.dumps(diagnostics)
        self.page().runJavaScript(f"window.set_editor_diagnostics({escaped});")

    def set_strategy_header_title(self, title: str) -> None:
        escaped = json.dumps(title or "")
        self.page().runJavaScript(f"window.set_strategy_header_title({escaped});")

    def show_rename_error(self, msg: str) -> None:
        """Show error message when rename fails."""
        escaped = json.dumps(msg)
        self.page().runJavaScript(f"window.show_rename_error({escaped});")

    def apply_chat_tabs_state(self, state_json: str) -> None:
        """Push full tabs state JSON to the JS side — used for cross-panel sync."""
        escaped = json.dumps(state_json)
        self.page().runJavaScript(f"applyChatTabsState({escaped});")
