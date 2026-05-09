import logging
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger("StrategyTester.gui.workers.ai_worker")

class AIAssistantWorker(QThread):
    chunk_received = Signal(str)
    error_occurred = Signal(str)
    finished_generation = Signal(str)

    def __init__(self, provider, api_key, prompt, code_context, error_traceback=None):
        super().__init__()
        self.provider = provider
        self.api_key = api_key
        self.prompt = prompt
        self.code_context = code_context
        self.error_traceback = error_traceback
        self.full_response = ""
        self.is_running = True

    def stop(self):
        self.is_running = False

    def run(self):
        if not self.api_key:
            self.error_occurred.emit("API Key is missing. Please set it in the Settings tab.")
            return

        system_prompt = (
            "You are an expert quantitative developer. You write Python backtesting strategies "
            "for the AGREGAT platform. Your code MUST be clean, well-documented, and inherit from "
            "`app.Strategy`. "
            "Provide explanations as normal text, but any generated code MUST be wrapped "
            "in standard Markdown python blocks (```python ... ```)."
        )

        user_content = f"Here is my current strategy code:\n```python\n{self.code_context}\n```\n\n"
        if self.error_traceback:
            user_content += f"I ran a backtest and got this error traceback:\n{self.error_traceback}\n\n"
        
        user_content += f"Task: {self.prompt}"

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
