import logging
import json
import os
import time
from typing import Any, Dict, Optional

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger("StrategyTester.gui.workers.ai_worker")
_APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_AI_GUIDE_PATH = os.path.join(_APP_DIR, "docs", "ai_strategy_authoring_guide.md")


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
        self.full_response = ""
        self.is_running = True
        self._stream_phase_emitted = False
        self._openai_sdk_client: Any = None
        self._openai_stream: Any = None
        self._anthropic_stream_ref: Any = None
        self._mistral_client_ref: Any = None
        self._gemini_client_ref: Any = None

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
        self.phase_changed.emit(msg)

    def _emit_chunk(self, text: str) -> None:
        self.full_response += text
        self._chunk_count += 1
        self.chunk_received.emit(text)

    def _finish_generation(self, *, reason: str = "complete") -> None:
        self.finished_generation.emit(self.full_response)

    def _on_first_stream_token(self) -> None:
        if self._stream_phase_emitted:
            return
        self._stream_phase_emitted = True
        self._phase("Streaming…")

    def run(self) -> None:
        self._chunk_count = 0
        self._stream_phase_emitted = False
        self._clear_http_refs()
        if not self.api_key:
            self.error_occurred.emit("API Key is missing. Please set it in the Settings tab.")
            return

        self._phase("Building prompt…")
        strategy_guide = ""
        try:
            with open(_AI_GUIDE_PATH, "r", encoding="utf-8") as f:
                strategy_guide = f.read().strip()
        except Exception:
            strategy_guide = ""

        system_prompt = (
            "You are AGREGAT AI assistant, an expert quantitative developer for Python trading strategies. "
            "Follow the user request exactly and do ONLY what was explicitly asked. "
            "Do not add extra features, unrelated suggestions, or off-topic explanations unless the user asks for them.\n\n"
            "## Core rules\n"
            "1) Be precise and reliable: provide correct, actionable output with no fluff.\n"
            "2) Keep responses focused and concise by default.\n"
            "3) Do not ask unnecessary questions. By default, proceed with sensible assumptions.\n"
            "4) Ask a question only if absolutely blocked and cannot produce a useful answer without it.\n"
            "5) If a question is unavoidable, ask only ONE short, high-impact question.\n"
            "6) Always answer in the same language the user used in their latest message.\n"
            "7) Preserve intent over style: prioritize exactly requested behavior and constraints.\n\n"
            "## How to modify code\n"
            "You must output the **complete new content of the file** inside a single Python fenced block:\n\n"
            "```python\n"
            "from engine.strategy.context import Strategy\n"
            "...\n"
            "```\n\n"
            "### Rules for the complete code block:\n"
            "- The block must contain the **entire** strategy file, not just the changed parts.\n"
            "- Keep ALL existing code that the user did not ask to change.\n"
            "- Apply all requested modifications precisely.\n"
            "- The code must be valid Python that inherits from `engine.strategy.context.Strategy`.\n"
            "- Use `super().__init__()` as the FIRST line inside `__init__` if your strategy has one.\n"
            "- Use `self.params[\"param_name\"]` to access strategy parameters (they are set by the framework).\n\n"
            "### When no code change is required\n"
            "If the user asks a question that does not require code changes, respond in plain text without any code block."
        )
        if strategy_guide:
            system_prompt += (
                "\n\nAGREGAT strategy authoring reference (must follow):\n"
                f"{strategy_guide}"
            )

        user_content = ""
        # Include chat history if provided
        chat_history = self.extra_context.get("chatHistory") if self.extra_context else None
        if chat_history and isinstance(chat_history, list) and len(chat_history) > 0:
            user_content += "## Chat history (most recent first)\n"
            for msg in reversed(chat_history[-20:]):
                role_label = "User" if msg.get("role") == "user" else ("Assistant" if msg.get("role") == "assistant" else "System")
                user_content += f"**{role_label}:** {msg.get('content', '')}\n\n"
        
        user_content += f"Here is my current strategy code:\n```python\n{self.code_context}\n```\n\n"
        if self.error_traceback:
            user_content += f"I ran a backtest and got this error traceback:\n{self.error_traceback}\n\n"

        user_content += f"Task: {self.prompt}"
        if self.extra_context and any(k != "chatHistory" for k in self.extra_context.keys()):
            ctx = self.extra_context
            # Build a human-readable description of chart selection data
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

        self._phase(
            f"Calling {self.provider}"
            + (f" · {self.model}" if self.model else "")
        )

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
                self._run_openai_compatible(system_prompt, user_content)
            elif "OpenAI" in self.provider:
                self._phase("OpenAI…")
                self._run_openai(system_prompt, user_content)
            elif "Anthropic" in self.provider:
                self._phase("Anthropic…")
                self._run_anthropic(system_prompt, user_content)
            elif "Mistral" in self.provider:
                self._phase("Mistral…")
                self._run_mistral(system_prompt, user_content)
            elif "Gemini" in self.provider:
                self._phase("Gemini…")
                self._run_gemini(system_prompt, user_content)
            else:
                self.error_occurred.emit(f"Unknown provider: {self.provider}")
        except ImportError as e:
            self.error_occurred.emit(f"Missing dependency: {e}. Run pip install.")
        except Exception as e:
            logger.exception("AI API Error")
            self.error_occurred.emit(str(e))

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
                        continue
                    if not chunk.choices:
                        continue
                    content = chunk.choices[0].delta.content
                    if content:
                        self._on_first_stream_token()
                        self._emit_chunk(content)
            finally:
                self._openai_stream = None
        finally:
            self._openai_sdk_client = None
        self._finish_generation(reason="openai_done")

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
                        continue
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    content = getattr(delta, "content", None) if delta is not None else None
                    if content:
                        self._on_first_stream_token()
                        self._emit_chunk(content)
            finally:
                self._openai_stream = None
        finally:
            self._openai_sdk_client = None
        self._finish_generation(reason="openai_compatible_done")

    def _run_anthropic(self, system_prompt: str, user_content: str) -> None:
        import anthropic

        model = self.model or "claude-3-5-sonnet-20240620"
        client = anthropic.Anthropic(api_key=self.api_key)
        self._phase("Waiting for API…")
        try:
            with client.messages.stream(
                max_tokens=4096,
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

    def _run_gemini(self, system_prompt: str, user_content: str) -> None:
        from google import genai
        from google.genai import types

        def _is_429(exc: BaseException) -> bool:
            t = str(exc).upper()
            return "429" in t or "RESOURCE_EXHAUSTED" in t

        def _free_tier_limit_zero(exc: BaseException) -> bool:
            s = str(exc).lower()
            return "limit: 0" in s and "free_tier" in s

        def _friendly_gemini_error(exc: BaseException) -> str:
            if _is_429(exc):
                return (
                    "Gemini 429: quota/rate limit. Try another model, billing, or wait. "
                    "https://ai.google.dev/gemini-api/docs/rate-limits"
                )
            return str(exc)

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
                    self.error_occurred.emit(_friendly_gemini_error(e))
                    return

            if last_error is not None:
                self.error_occurred.emit(_friendly_gemini_error(last_error))
        finally:
            self._gemini_client_ref = None
