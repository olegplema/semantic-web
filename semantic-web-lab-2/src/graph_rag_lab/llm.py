from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class HTTPModel:
    def __init__(self, provider: str, model: str | None = None):
        if provider not in {"ollama", "gemini"}:
            raise ValueError("Доступні LLM: ollama, gemini")
        self.provider = provider
        self.model = model or ("gemma3:4b" if provider == "ollama" else "gemini-2.5-flash")

    def generate(self, prompt: str) -> str:
        if self.provider == "ollama":
            base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
            url = f"{base}/api/generate"
            payload = {"model": self.model, "prompt": prompt, "stream": False, "options": {"temperature": 0}}
            headers = {"Content-Type": "application/json"}
        else:
            key = os.getenv("GEMINI_API_KEY")
            if not key:
                raise RuntimeError("Задайте GEMINI_API_KEY у змінних середовища")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0}}
            headers = {"Content-Type": "application/json", "x-goog-api-key": key}
        request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urlopen(request, timeout=180) as response:
                result = json.load(response)
        except (HTTPError, URLError, TimeoutError) as error:
            raise RuntimeError(f"Не вдалося отримати відповідь {self.provider}: {error}") from error
        if self.provider == "ollama":
            return result["response"].strip()
        return "\n".join(part["text"] for item in result["candidates"] for part in item["content"]["parts"] if "text" in part).strip()
