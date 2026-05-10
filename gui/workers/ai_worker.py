import logging
import json
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger("StrategyTester.gui.workers.ai_worker")

class AIAssistantWorker(QThread):
    chunk_received = Signal(str)
    error_occurred = Signal(str)
    finished_generation = Signal(str)

    def __init__(self, provider, api_key, prompt, code_context, error_traceback=None, extra_context=None):
        super().__init__()
        self.provider = provider
        self.api_key = api_key
        self.prompt = prompt
        self.code_context = code_context
        self.error_traceback = error_traceback
        self.extra_context = extra_context or {}
        self.full_response = ""
        self.is_running = True

    def stop(self):
        self.is_running = False

    def run(self):
        if not self.api_key:
            self.error_occurred.emit("API Key is missing. Please set it in the Settings tab.")
            return

        system_prompt = (
            "You are AGREGAT AI assistant, an expert quantitative developer for Python trading strategies. "
            "Follow the user request exactly and do ONLY what was explicitly asked. "
            "Do not add extra features, unrelated suggestions, or off-topic explanations unless the user asks for them.\n\n"
            "Execution rules:\n"
            "1) Be precise and reliable: provide correct, actionable output with no fluff.\n"
            "2) Keep responses focused and concise by default.\n"
            "3) Do not ask unnecessary questions. By default, proceed with sensible assumptions.\n"
            "4) Ask a question only if absolutely blocked and cannot produce a useful answer without it.\n"
            "5) If a question is unavoidable, ask only ONE short, high-impact question.\n"
            "6) For AGREGAT code, generate clean, production-ready Python that inherits from `app.Strategy`.\n"
            "7) Any generated code MUST be wrapped in Markdown python blocks (```python ... ```).\n"
            "8) If no code is required, respond in plain text.\n"
            "9) Always answer in the same language the user used in their latest message.\n"
            "10) Preserve intent over style: prioritize exactly requested behavior and constraints."
        )

        user_content = f"Here is my current strategy code:\n```python\n{self.code_context}\n```\n\n"
        if self.error_traceback:
            user_content += f"I ran a backtest and got this error traceback:\n{self.error_traceback}\n\n"
        
        user_content += f"Task: {self.prompt}"
        if self.extra_context:
            user_content += (
                "\n\nAdditional chart context (JSON):\n"
                f"```json\n{json.dumps(self.extra_context, ensure_ascii=False, indent=2)}\n```"
            )

        try:
            if "OpenAI" in self.provider:
                self._run_openai(system_prompt, user_content)
            elif "Anthropic" in self.provider:
                self._run_anthropic(system_prompt, user_content)
            elif "Mistral" in self.provider:
                self._run_mistral(system_prompt, user_content)
            else:
                self.error_occurred.emit(f"Unknown provider: {self.provider}")
        except ImportError as e:
            self.error_occurred.emit(f"Missing dependency: {e}. Run pip install.")
        except Exception as e:
            logger.exception("AI API Error")
            self.error_occurred.emit(str(e))

    def _run_openai(self, system_prompt, user_content):
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            stream=True
        )
        for chunk in response:
            if not self.is_running:
                break
            content = chunk.choices[0].delta.content
            if content:
                self.full_response += content
                self.chunk_received.emit(content)
        self.finished_generation.emit(self.full_response)

    def _run_anthropic(self, system_prompt, user_content):
        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key)
        with client.messages.stream(
            max_tokens=4096,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_content}
            ],
            model="claude-3-5-sonnet-20240620",
        ) as stream:
            for text in stream.text_stream:
                if not self.is_running:
                    break
                self.full_response += text
                self.chunk_received.emit(text)
        self.finished_generation.emit(self.full_response)

    def _run_mistral(self, system_prompt, user_content):
        from mistralai.client import Mistral
        client = Mistral(api_key=self.api_key)
        response = client.chat.stream(
            model="mistral-large-latest",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ]
        )
        for chunk in response:
            if not self.is_running:
                break
            content = chunk.data.choices[0].delta.content
            if content:
                self.full_response += content
                self.chunk_received.emit(content)
        self.finished_generation.emit(self.full_response)
