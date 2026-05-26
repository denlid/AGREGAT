import logging
import json
import os
import time
from typing import Any, Dict, Optional

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger("StrategyTester.gui.workers.ai_worker")
_APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_AI_GUIDE_PATH = os.path.join(_APP_DIR, "docs", "ai_strategy_authoring_guide.md")
_PROMPTS_DIR = os.path.join(_APP_DIR, "config", "prompts")


def _detect_task_type(prompt: str, has_error: bool) -> str:
    """Detect what kind of task this is to pick the right prompt template."""
    prompt_lower = prompt.lower().strip()
    if has_error or any(w in prompt_lower for w in ("fix", "error", "bug", "traceback", "broken", "not working", "issue")):
        return "fix"
    if any(w in prompt_lower for w in ("explain", "what does", "how does", "why", "describe", "tell me about")):
        return "explain"
    return "write"


def _read_prompt_file(*path_parts: str) -> str:
    """Read a text file, returning empty string on failure."""
    p = os.path.join(*path_parts)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


class AIAssistantWorker(QThread):
    chunk_received = Signal(str)
    error_occurred = Signal(str)
    finished_generation = Signal(str)
    phase_changed = Signal(str)  # Short English status for the chat UI

    def __init__(
        self,
        *,
        provider: str,
        api_key: str,
        model: str,
        prompt: str,
        code_context: str,
        error_traceback: Optional[str] = None,
        extra_context: Optional[Dict[str, Any]] = None,
        openai_base_url: str = "",
        openai_extra_body: Optional[Dict[str, Any]] = None,
        max_tokens: int = 16384,
    ) -> None:
        super().__init__()
        self._chunk_count = 0
        self.provider = provider
        self.api_key = api_key
        self.model = (model or "").strip()
        self.prompt = prompt
        self.code_context = code_context
        self.error_traceback = error_traceback
        self.extra_context = extra_context or {}
        self.openai_base_url = (openai_base_url or "").strip()
        self.openai_extra_body = openai_extra_body
        self.max_tokens = max_tokens
        self.full_response = ""
        self.is_running = True
        self._stream_phase_emitted = False
        self._openai_sdk_client: Any = None
        self._openai_stream: Any = None
        self._anthropic_stream_ref: Any = None
        self._mistral_client_ref: Any = None
        self._gemini_client_ref: Any = None
        self._start_time: float = 0.0
        self._phase_msg: str = ""
        # Track last finish_reason from API for better error messages
        self._last_finish_reason: str | None = None

    def stop(self) -> None:
        self.is_running = False
        self._abort_inflight_http()

    def _clear_http_refs(self) -> None:
        self._openai_sdk_client = None
        self._openai_stream = None
        self._anthropic_stream_ref = None
        self._mistral_client_ref = None
        self._gemini_client_ref = None

    def _abort_inflight_http(self) -> None:
        """Best-effort: close sockets so a blocked ``create()`` / stream read unblocks after Stop."""
        s = self._openai_stream
        if s is not None:
            try:
                resp = getattr(s, "response", None)
                if resp is not None and hasattr(resp, "close"):
                    resp.close()
            except Exception:
                logger.debug("abort OpenAI stream.response", exc_info=True)
        c = self._openai_sdk_client
        if c is not None:
            try:
                close = getattr(c, "close", None)
                if callable(close):
                    close()
            except Exception:
                logger.debug("abort OpenAI client", exc_info=True)
        a = self._anthropic_stream_ref
        if a is not None:
            try:
                close = getattr(a, "close", None)
                if callable(close):
                    close()
            except Exception:
                logger.debug("abort Anthropic stream", exc_info=True)
        m = self._mistral_client_ref
        if m is not None:
            try:
                close = getattr(m, "close", None)
                if callable(close):
                    close()
            except Exception:
                logger.debug("abort Mistral client", exc_info=True)
        g = self._gemini_client_ref
        if g is not None:
            try:
                close = getattr(g, "close", None)
                if callable(close):
                    close()
            except Exception:
                logger.debug("abort Gemini client", exc_info=True)

    def _phase(self, msg: str) -> None:
        self._phase_msg = msg
        self.phase_changed.emit(msg)

    def _emit_chunk(self, text: str) -> None:
        self.full_response += text
        self._chunk_count += 1
        self.chunk_received.emit(text)

    def _finish_generation(self, *, reason: str = "complete") -> None:
        if self._chunk_count == 0 and self.is_running and not self.full_response:
            api_reason = self._last_finish_reason or "unknown"
            logger.warning("AI generation finished with 0 chunks and empty full_response (api_reason=%s, internal_reason=%s)", api_reason, reason)
            
            if api_reason == "content_filter":
                self.error_occurred.emit(
                    "⚠️ OpenAI Safety Filter заблокував відповідь.\n\n"
                    "Модель вважає, що ваш запит порушує політику безпеки.\n\n"
                    "Що робити:\n"
                    "• Замість «видали» напишіть «заміни» або «перепиши»\n"
                    "• Уникайте формулювань, які можуть бути сприйняті як деструктивні\n"
                    "• Розбийте складний запит на кілька простих\n\n"
                    "Приклад: «Напиши нову стратегію за регламентом нижче»"
                )
            elif api_reason == "stop":
                self.error_occurred.emit(
                    "⚠️ AI повернуло порожню відповідь (finish_reason=stop).\n\n"
                    "Модель отримала запит, але вирішила нічого не відповідати.\n\n"
                    "Це часто трапляється коли:\n"
                    "• Модель не розуміє, що саме потрібно зробити\n"
                    "• Запит містить суперечливі інструкції\n"
                    "• Системний prompt конфліктує з запитом\n\n"
                    "Спробуйте переформулювати — замість «видали» напишіть «заміни»"
                )
            elif api_reason == "length":
                self.error_occurred.emit(
                    "⚠️ AI не змогло згенерувати відповідь — досягнуто ліміту токенів (finish_reason=length).\n\n"
                    f"Поточний ліміт: {self.max_tokens} токенів.\n\n"
                    "Можливі причини:\n"
                    "• Ваш промпт (системний + код + регламент + історія) надто великий\n"
                    "• Модель не встигає завершити генерацію в межах ліміту\n\n"
                    "Спробуйте:\n"
                    "1. Скоротити промпт — прибрати зайвий контекст, історію\n"
                    "2. Використати модель з більшим контекстом\n"
                    "3. Розбити завдання на частини"
                )
            else:
                self.error_occurred.emit(
                    f"🤖 AI повернуло порожню відповідь.\n"
                    f"API finish_reason: {api_reason}\n\n"
                    f"Можливі причини:\n"
                    f"• Спрацював safety filter моделі\n"
                    f"• Некоректний prompt або контекст\n"
                    f"• Проблеми з моделлю або провайдером\n\n"
                    f"Спробуйте:\n"
                    f"1. Замість «видали» напишіть «заміни»\n"
                    f"2. Зменшити розмір запиту\n"
                    f"3. Перевірити консоль (F12) для деталей"
                )
            return
        self.finished_generation.emit(self.full_response)

    def _on_first_stream_token(self) -> None:
        if self._stream_phase_emitted:
            return
        self._stream_phase_emitted = True
        self._phase("Receiving response…")

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------
    def _build_system_prompt(self) -> str:
        """Build system prompt from base template + task-specific template + guide."""
        base = _read_prompt_file(_PROMPTS_DIR, "system_base.md")
        if not base:
            # Fallback if file missing
            base = "You are AGREGAT AI assistant, an expert quantitative developer for Python trading strategies."

        task_type = _detect_task_type(self.prompt, bool(self.error_traceback))
        task_template = _read_prompt_file(_PROMPTS_DIR, f"system_{task_type}.md")
        if task_template:
            # Inject error traceback if this is a fix prompt
            if task_type == "fix" and self.error_traceback:
                task_template = task_template.replace("{error_traceback}", self.error_traceback)
            base += "\n\n" + task_template

        strategy_guide = _read_prompt_file(_AI_GUIDE_PATH)
        if strategy_guide:
            base += "\n\n## AGREGAT strategy authoring reference (must follow)\n" + strategy_guide

        # Backtest metrics if available
        metrics = self.extra_context.get("backtest_metrics") if self.extra_context else None
        if metrics and isinstance(metrics, dict):
            metrics_lines = "\n".join(f"- {k}: {v}" for k, v in metrics.items())
            base += f"\n\n## Last backtest results\n{metrics_lines}\n"

        # Data schema if available
        data_schema = self.extra_context.get("data_schema") if self.extra_context else None
        if data_schema and isinstance(data_schema, dict):
            schema_json = json.dumps(data_schema, ensure_ascii=False, indent=2)
            base += f"\n\n## Loaded data schema\n```json\n{schema_json}\n```\n"

        return base

    def _build_user_content(self) -> str:
        """Build the user message content from context, code, prompt."""
        user_content = ""

        # Include chat history if provided
        chat_history = self.extra_context.get("chatHistory") if self.extra_context else None
        if chat_history and isinstance(chat_history, list) and len(chat_history) > 0:
            user_content += "## Chat history (most recent first)\n"
            for msg in reversed(chat_history[-20:]):
                role_label = "User" if msg.get("role") == "user" else ("Assistant" if msg.get("role") == "assistant" else "System")
                user_content += f"**{role_label}:** {msg.get('content', '')}\n\n"

        if self.code_context:
            user_content += f"Here is my current strategy code:\n```python\n{self.code_context}\n```\n\n"

        if self.error_traceback:
            user_content += f"I ran a backtest and got this error traceback:\n{self.error_traceback}\n\n"

        user_content += f"Task: {self.prompt}"

        # Chart selection data
        if self.extra_context and any(k not in ("chatHistory", "backtest_metrics", "data_schema") for k in self.extra_context.keys()):
            ctx = self.extra_context
            has_chart_data = any(k in ctx for k in ("symbol", "timeframe", "selected_range", "price_range", "visible_candles", "indicators", "selection_info"))
            if has_chart_data:
                chart_desc = ""
                if ctx.get("symbol"):
                    chart_desc += f"- Symbol: {ctx['symbol']}\n"
                if ctx.get("timeframe"):
                    chart_desc += f"- Timeframe: {ctx['timeframe']}\n"
                if ctx.get("selected_range"):
                    r = ctx["selected_range"]
                    chart_desc += f"- Selected date range: {r.get('start','?')} to {r.get('end','?')}\n"
                if ctx.get("price_range"):
                    pr = ctx["price_range"]
                    chart_desc += f"- Price range: {pr.get('low','?')} to {pr.get('high','?')}\n"
                if ctx.get("visible_candles"):
                    chart_desc += f"- Visible candles on chart: ~{ctx['visible_candles']}\n"
                if ctx.get("indicators"):
                    chart_desc += f"- Indicators displayed: {', '.join(ctx['indicators'])}\n"
                if ctx.get("selection_info"):
                    chart_desc += f"- Selection details: {ctx['selection_info']}\n"

                if chart_desc:
                    user_content += (
                        "\n\n## Chart selection data\n"
                        "The user selected an area on the price chart. This data describes "
                        "the currently visible chart area and the user's selection.\n\n"
                        f"{chart_desc}\n"
                        "Use this context to understand which part of the market the user "
                        "is looking at. For example: which symbol, what timeframe, what "
                        "price range is currently visible.\n\n"
                        "Full raw context (JSON):\n"
                        f"```json\n{json.dumps(self.extra_context, ensure_ascii=False, indent=2)}\n```"
                    )
        return user_content

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------
    def run(self) -> None:
        self._chunk_count = 0
        self._stream_phase_emitted = False
        self._clear_http_refs()
        self._start_time = time.time()
        if not self.api_key:
            self.error_occurred.emit("API Key is missing. Please set it in the Settings tab.")
            return

        self._phase("Building prompt…")
        system_prompt = self._build_system_prompt()
        user_content = self._build_user_content()

        provider_label = self.provider + (f" · {self.model}" if self.model else "")
        self._phase(f"Calling {provider_label}")

        try:
            if self.provider == "Custom (OpenAI-compatible)":
                if not self.openai_base_url:
                    self.error_occurred.emit(
                        "Custom provider: set Base URL in Settings → AI "
                        "(e.g. https://integrate.api.nvidia.com/v1)."
                    )
                    return
                if not self.model:
                    self.error_occurred.emit("Custom provider: enter a model id in Settings → AI.")
                    return
                self._phase("Custom endpoint…")
                self._run_openai_compatible_safe(system_prompt, user_content)
            elif "OpenAI" in self.provider:
                self._phase("OpenAI connecting…")
                self._run_openai_with_retry(system_prompt, user_content)
            elif "Anthropic" in self.provider:
                self._phase("Anthropic connecting…")
                self._run_anthropic_with_retry(system_prompt, user_content)
            elif "Mistral" in self.provider:
                self._phase("Mistral connecting…")
                self._run_mistral_with_retry(system_prompt, user_content)
            elif "Gemini" in self.provider:
                self._phase("Gemini connecting…")
                self._run_gemini_with_retry(system_prompt, user_content)
            else:
                self.error_occurred.emit(f"Unknown provider: {self.provider}")
        except ImportError as e:
            self.error_occurred.emit(f"Missing dependency: {e}. Run pip install.")
        except Exception as e:
            logger.exception("AI API Error")
            self.error_occurred.emit(str(e))

    # ------------------------------------------------------------------
    # Retry wrappers (2.1)
    # ------------------------------------------------------------------
    def _retry_429(self, provider_name: str, fn, *args, max_retries: int = 3):
        """Generic 429 retry wrapper with exponential backoff."""
        last_error: Exception | None = None
        for attempt in range(max_retries):
            if not self.is_running:
                self._finish_generation(reason=f"{provider_name}_cancelled_during_retry")
                return
            try:
                if attempt > 0:
                    self._phase(f"{provider_name} retry {attempt + 1}/{max_retries}…")
                fn(*args)
                return  # success
            except Exception as e:
                last_error = e
                if not self.is_running:
                    self._finish_generation(reason=f"{provider_name}_cancelled_during_retry")
                    return
                estr = str(e).upper()
                is_429 = "429" in estr or "RATE_LIMIT" in estr or "RESOURCE_EXHAUSTED" in estr
                if is_429 and attempt < max_retries - 1:
                    self._phase(f"429 backoff ({attempt + 1}/{max_retries})…")
                    time.sleep(2.0 + attempt * 4.0)
                    continue
                # Not a 429, or out of retries — re-raise
                raise
        # If we exhausted retries, raise the last error
        if last_error is not None:
            raise last_error

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------
    def _run_openai_with_retry(self, system_prompt: str, user_content: str) -> None:
        def call():
            self._run_openai(system_prompt, user_content)
        try:
            self._retry_429("OpenAI", call)
        except Exception as e:
            self._handle_openai_error(e)

    def _handle_openai_error(self, e: Exception) -> None:
        estr = str(e)
        try:
            import openai
            if isinstance(e, openai.AuthenticationError):
                self.error_occurred.emit(
                    "OpenAI: Invalid API key. Check Settings → AI → API Key.\n"
                    "Expected format: sk-... (starts with 'sk-')"
                )
                return
            if isinstance(e, openai.RateLimitError):
                self.error_occurred.emit(
                    "OpenAI: Rate limit exceeded (429).\n"
                    "Wait 30-60 seconds or upgrade your plan.\n"
                    "https://platform.openai.com/account/rate-limits"
                )
                return
            if isinstance(e, openai.BadRequestError):
                if "context_length_exceeded" in estr or "maximum context length" in estr:
                    total_tokens = len(estr)
                    self.error_occurred.emit(
                        f"OpenAI: Context too long (~{total_tokens} tokens). "
                        f"Model max is unknown. Try:\n"
                        "1) Reduce code size\n"
                        "2) Use a model with larger context (e.g. gpt-4-1106-preview)"
                    )
                    return
                self.error_occurred.emit(f"OpenAI BadRequest: {e}")
                return
            if isinstance(e, openai.APITimeoutError):
                self.error_occurred.emit("OpenAI: Request timed out. Check your internet connection.")
                return
            if isinstance(e, openai.APIConnectionError):
                self.error_occurred.emit("OpenAI: Connection failed. Check proxy/VPN settings.")
                return
        except ImportError:
            pass
        self.error_occurred.emit(f"OpenAI error: {e}")

    def _run_openai(self, system_prompt: str, user_content: str) -> None:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key)
        self._openai_sdk_client = client
        model = self.model or "gpt-4o"
        self._phase("Waiting for API…")
        try:
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    stream=True,
                    max_tokens=self.max_tokens,
                )
            except Exception:
                if not self.is_running:
                    self._finish_generation(reason="cancelled_or_error_before_stream_openai")
                    return
                raise
            self._openai_stream = response
            try:
                for chunk in response:
                    if not self.is_running:
                        break
                    if not getattr(chunk, "choices", None):
                        logger.debug("OpenAI: chunk without choices attr, skipping")
                        continue
                    if not chunk.choices:
                        # Shouldn't happen in normal API, but log it
                        logger.debug("OpenAI: chunk with empty choices list (id=%s)", getattr(chunk, 'id', '?'))
                        continue
                    # Always save finish_reason when present (even "stop", "length", "content_filter")
                    finish_reason = getattr(chunk.choices[0], 'finish_reason', None)
                    if finish_reason is not None:
                        self._last_finish_reason = finish_reason
                        logger.info("OpenAI: chunk finish_reason=%s (chunk_count=%d)", finish_reason, self._chunk_count)
                    content = chunk.choices[0].delta.content
                    if content:
                        self._on_first_stream_token()
                        self._emit_chunk(content)
                    else:
                        logger.debug("OpenAI: chunk with no delta.content (finish_reason=%s)", finish_reason)
            finally:
                self._openai_stream = None
        finally:
            self._openai_sdk_client = None
        self._finish_generation(reason="openai_done")

    def _run_openai_compatible_safe(self, system_prompt: str, user_content: str) -> None:
        """Run custom provider with retry + error handling."""
        def call():
            self._run_openai_compatible(system_prompt, user_content)
        try:
            self._retry_429("Custom", call)
        except Exception as e:
            self._handle_openai_error(e)

    def _run_openai_compatible(self, system_prompt: str, user_content: str) -> None:
        from openai import OpenAI

        base = self.openai_base_url.rstrip("/")
        client = OpenAI(api_key=self.api_key, base_url=base)
        self._openai_sdk_client = client
        model = self.model
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "stream": True,
            "max_tokens": self.max_tokens,
        }
        if self.openai_extra_body:
            kwargs["extra_body"] = self.openai_extra_body
        self._phase("Waiting for API…")
        try:
            try:
                response = client.chat.completions.create(**kwargs)
            except Exception:
                if not self.is_running:
                    self._finish_generation(reason="cancelled_or_error_before_stream_compat")
                    return
                raise
            self._openai_stream = response
            try:
                for chunk in response:
                    if not self.is_running:
                        break
                    if not getattr(chunk, "choices", None):
                        logger.debug("Custom: chunk without choices attr, skipping")
                        continue
                    if not chunk.choices:
                        logger.debug("Custom: chunk with empty choices list (id=%s)", getattr(chunk, 'id', '?'))
                        continue
                    # Track finish_reason (same as standard OpenAI)
                    finish_reason = getattr(chunk.choices[0], 'finish_reason', None)
                    if finish_reason is not None:
                        self._last_finish_reason = finish_reason
                        logger.info("Custom: chunk finish_reason=%s (chunk_count=%d)", finish_reason, self._chunk_count)
                    delta = chunk.choices[0].delta
                    content = getattr(delta, "content", None) if delta is not None else None
                    if content:
                        self._on_first_stream_token()
                        self._emit_chunk(content)
                    else:
                        logger.debug("Custom: chunk with no delta.content (finish_reason=%s)", finish_reason)
            finally:
                self._openai_stream = None
        finally:
            self._openai_sdk_client = None
        self._finish_generation(reason="openai_compatible_done")

    # ------------------------------------------------------------------
    # Anthropic
    # ------------------------------------------------------------------
    def _run_anthropic_with_retry(self, system_prompt: str, user_content: str) -> None:
        def call():
            self._run_anthropic(system_prompt, user_content)
        try:
            self._retry_429("Anthropic", call)
        except Exception as e:
            self._handle_anthropic_error(e)

    def _handle_anthropic_error(self, e: Exception) -> None:
        estr = str(e).lower()
        try:
            import anthropic
            if isinstance(e, anthropic.APIConnectionError):
                self.error_occurred.emit("Anthropic: Connection failed. Check your internet.")
                return
            if isinstance(e, anthropic.RateLimitError):
                self.error_occurred.emit("Anthropic: Rate limit exceeded. Wait 30s.")
                return
            if isinstance(e, anthropic.BadRequestError):
                if "too many tokens" in estr or "context" in estr:
                    self.error_occurred.emit("Anthropic: Prompt too long. Reduce code size.")
                    return
                if "maximum" in estr and "tokens" in estr:
                    self.error_occurred.emit("Anthropic: max_tokens exceeds model limit.")
                    return
                self.error_occurred.emit(f"Anthropic BadRequest: {e}")
                return
            if isinstance(e, anthropic.AuthenticationError):
                self.error_occurred.emit("Anthropic: Invalid API key. Check Settings → AI.")
                return
        except ImportError:
            pass
        self.error_occurred.emit(f"Anthropic error: {e}")

    def _run_anthropic(self, system_prompt: str, user_content: str) -> None:
        import anthropic

        model = self.model or "claude-3-5-sonnet-20240620"
        client = anthropic.Anthropic(api_key=self.api_key)
        self._phase("Waiting for API…")
        try:
            with client.messages.stream(
                max_tokens=self.max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
                model=model,
            ) as stream:
                self._anthropic_stream_ref = stream
                try:
                    for text in stream.text_stream:
                        if not self.is_running:
                            break
                        self._on_first_stream_token()
                        self._emit_chunk(text)
                finally:
                    self._anthropic_stream_ref = None
        except Exception:
            if not self.is_running:
                self._finish_generation(reason="anthropic_cancelled")
                return
            raise
        self._finish_generation(reason="anthropic_done")

    # ------------------------------------------------------------------
    # Mistral
    # ------------------------------------------------------------------
    def _run_mistral_with_retry(self, system_prompt: str, user_content: str) -> None:
        def call():
            self._run_mistral(system_prompt, user_content)
        try:
            self._retry_429("Mistral", call)
        except Exception as e:
            self._handle_mistral_error(e)

    def _handle_mistral_error(self, e: Exception) -> None:
        estr = str(e).lower()
        if "401" in estr or "api key" in estr or "unauthorized" in estr:
            self.error_occurred.emit("Mistral: Invalid API key. Check Settings → AI.")
            return
        if "429" in estr or "rate" in estr:
            self.error_occurred.emit("Mistral: Rate limit. Wait 30s.")
            return
        if "context" in estr or "length" in estr or "too large" in estr:
            self.error_occurred.emit("Mistral: Prompt too long. Reduce code size.")
            return
        self.error_occurred.emit(f"Mistral error: {e}")

    def _run_mistral(self, system_prompt: str, user_content: str) -> None:
        from mistralai.client import Mistral

        model = self.model or "mistral-large-latest"
        client = Mistral(api_key=self.api_key)
        self._mistral_client_ref = client
        self._phase("Waiting for API…")
        try:
            try:
                response = client.chat.stream(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                )
            except Exception:
                if not self.is_running:
                    self._finish_generation(reason="mistral_cancelled_before_stream")
                    return
                raise
            for chunk in response:
                if not self.is_running:
                    break
                content = chunk.data.choices[0].delta.content
                if content:
                    self._on_first_stream_token()
                    self._emit_chunk(content)
        finally:
            self._mistral_client_ref = None
        self._finish_generation(reason="mistral_done")

    # ------------------------------------------------------------------
    # Gemini
    # ------------------------------------------------------------------
    def _run_gemini_with_retry(self, system_prompt: str, user_content: str) -> None:
        # Gemini already has its own retry logic — keep it but also wrap with _retry_429
        try:
            self._run_gemini(system_prompt, user_content)
        except Exception as e:
            self._handle_gemini_error(e)

    def _handle_gemini_error(self, e: Exception) -> None:
        estr = str(e).upper()
        if "API_KEY_INVALID" in estr or "API_KEY" in estr:
            self.error_occurred.emit("Gemini: Invalid API key. Check Settings → AI.")
            return
        if "SAFETY" in estr:
            self.error_occurred.emit("Gemini: Response blocked by safety filters. Try rephrasing.")
            return
        if "429" in estr or "RESOURCE_EXHAUSTED" in estr:
            self.error_occurred.emit(
                "Gemini 429: quota/rate limit. Try another model or wait.\n"
                "https://ai.google.dev/gemini-api/docs/rate-limits"
            )
            return
        if "PERMISSION_DENIED" in estr:
            self.error_occurred.emit("Gemini: API not enabled. Enable in Google Cloud Console.")
            return
        self.error_occurred.emit(f"Gemini error: {e}")

    def _run_gemini(self, system_prompt: str, user_content: str) -> None:
        from google import genai
        from google.genai import types

        def _is_429(exc: BaseException) -> bool:
            t = str(exc).upper()
            return "429" in t or "RESOURCE_EXHAUSTED" in t

        def _free_tier_limit_zero(exc: BaseException) -> bool:
            s = str(exc).lower()
            return "limit: 0" in s and "free_tier" in s

        model = (self.model or "gemini-2.5-flash").strip()
        client = genai.Client(api_key=self.api_key)
        self._gemini_client_ref = client
        config = types.GenerateContentConfig(system_instruction=system_prompt)
        last_error: BaseException | None = None

        try:
            for attempt in range(3):
                if not self.is_running:
                    self._finish_generation(reason="gemini_outer_cancelled")
                    return
                try:
                    if attempt > 0:
                        self._phase(f"Gemini retry {attempt + 1}/3…")
                    else:
                        self._phase("Waiting for API…")
                    # Only reset on first attempt; keep partial data on retry
                    if attempt == 0:
                        self.full_response = ""
                        self._chunk_count = 0
                    stream = client.models.generate_content_stream(
                        model=model,
                        contents=user_content,
                        config=config,
                    )
                    for chunk in stream:
                        if not self.is_running:
                            break
                        text = getattr(chunk, "text", None) or ""
                        if text:
                            self._on_first_stream_token()
                            self._emit_chunk(text)
                        else:
                            finish_reason = getattr(chunk, 'finish_reason', None) or getattr(chunk, 'candidates', None)
                            logger.debug("Gemini: chunk with empty text (finish_reason=%s)", finish_reason)
                    self._finish_generation(reason=f"gemini_attempt_{attempt + 1}_ok")
                    return
                except Exception as e:
                    last_error = e
                    if not self.is_running:
                        self._finish_generation(reason="gemini_ex_while_cancelled")
                        return
                    if _free_tier_limit_zero(e):
                        break
                    if _is_429(e) and attempt < 2:
                        self._phase("429 backoff…")
                        time.sleep(6.0 + attempt * 4.0)
                        continue
                    if _is_429(e):
                        break
                    # Don't emit error here — let _handle_gemini_error do it via outer wrapper
                    raise

            if last_error is not None:
                raise last_error
        finally:
            self._gemini_client_ref = None
