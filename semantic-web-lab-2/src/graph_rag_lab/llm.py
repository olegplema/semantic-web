from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import re
import tempfile
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "llm"
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
logger = logging.getLogger(__name__)


class HTTPModel:
    def __init__(
        self, provider: str, model: str | None = None, *,
        cache_dir: Path | str = DEFAULT_CACHE_DIR, max_attempts: int = 5,
    ):
        if provider not in {"ollama", "gemini"}:
            raise ValueError("Доступні LLM: ollama, gemini")
        if max_attempts < 1:
            raise ValueError("max_attempts має бути >= 1")
        self.provider = provider
        self.model = model or ("gemma3:4b" if provider == "ollama" else "gemini-3.5-flash-lite")
        self.cache_dir = Path(cache_dir)
        self.max_attempts = max_attempts
        if provider == "ollama":
            base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
            self.url = f"{base}/api/generate"
        else:
            self.url = f"https://generativelanguage.googleapis.com/v1beta/models/{quote(self.model, safe='')}:generateContent"

    @staticmethod
    def _redact(text: str) -> str:
        key = os.getenv("GEMINI_API_KEY")
        if key:
            for value in (key, quote(key, safe="")):
                text = text.replace(value, "[REDACTED]")
        text = re.sub(r"AIza[\w-]+", "[REDACTED]", text)
        return re.sub(r"(?i)((?:x-goog-api-key|api[_-]?key|key)\s*[=:]\s*)[^\s&\"'<>]+", r"\1[REDACTED]", text)

    def _cache_path(self, prompt: str) -> Path:
        identity = {
            "version": 1, "provider": self.provider, "model": self.model,
            "endpoint": self.url, "prompt": prompt, "temperature": 0,
        }
        digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, path: Path) -> str | None:
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (ValueError, UnicodeError):
            logger.warning("Пошкоджений запис LLM-кешу %s; запит буде виконано повторно", path.name)
            return None
        except OSError as error:
            raise RuntimeError(self._redact(f"Не вдалося прочитати LLM-кеш: {error}")) from None
        if isinstance(entry, dict) and isinstance(entry.get("text"), str) and entry["text"].strip():
            return self._redact(entry["text"])
        return None

    def _write_cache(self, path: Path, text: str) -> None:
        temporary = None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache_dir, delete=False) as file:
                temporary = Path(file.name)
                json.dump({"text": text}, file, ensure_ascii=False)
            temporary.replace(path)
        except OSError as error:
            raise RuntimeError(self._redact(f"Не вдалося зберегти LLM-кеш: {error}")) from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def invalidate_cache(self, prompt: str) -> None:
        self._cache_path(prompt).unlink(missing_ok=True)

    def _api_error(self, status: int, reason: str, payload: object) -> str:
        message = f"Не вдалося отримати відповідь {self.provider}: HTTP {status} {reason}"
        error = payload.get("error", payload) if isinstance(payload, dict) else payload
        if isinstance(error, dict):
            if error.get("status"):
                message += f"; {error['status']}"
            if error.get("message"):
                message += f"; {error['message']}"
            if error.get("details"):
                message += f"; деталі API (квоти/повтор): {json.dumps(error['details'], ensure_ascii=False)}"
        elif error:
            message += f"; {str(error)[:4000]}"
        return self._redact(message)

    @staticmethod
    def _retry_delay(attempt: int, retry_after: str | None, payload: object) -> float:
        delay = min(60.0, 2.0 ** min(attempt, 6) + random.uniform(0, 1))
        server_delays = []
        if retry_after:
            try:
                server_delays.append(float(retry_after))
            except ValueError:
                try:
                    date = parsedate_to_datetime(retry_after)
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                    server_delays.append(date.timestamp() - time.time())
                except (ValueError, TypeError, OverflowError):
                    pass
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        details = error.get("details", []) if isinstance(error, dict) else []
        if isinstance(details, list):
            for detail in details:
                if isinstance(detail, dict) and str(detail.get("@type", "")).endswith("google.rpc.RetryInfo"):
                    duration = detail.get("retryDelay", "")
                    if isinstance(duration, str) and duration.endswith("s"):
                        try:
                            server_delays.append(float(duration[:-1]))
                        except ValueError:
                            pass
        return max([delay] + [value for value in server_delays if math.isfinite(value) and value >= 0])

    def _response_text(self, result: object) -> str:
        if not isinstance(result, dict):
            raise RuntimeError(f"Некоректна відповідь {self.provider}: очікується JSON-об'єкт")
        if result.get("error"):
            raise RuntimeError(self._api_error(200, "", result))
        if self.provider == "ollama":
            text = result.get("response")
            if result.get("done") is False:
                text = None
        else:
            feedback = result.get("promptFeedback", {})
            if isinstance(feedback, dict) and feedback.get("blockReason"):
                raise RuntimeError(self._redact(f"Gemini заблокував запит: {feedback['blockReason']}"))
            candidates = result.get("candidates")
            if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
                raise RuntimeError("Gemini не повернув кандидата відповіді")
            candidate = candidates[0]
            finish = candidate.get("finishReason")
            if finish not in (None, "STOP"):
                raise RuntimeError(self._redact(f"Gemini не завершив відповідь: finishReason={finish}"))
            content = candidate.get("content", {})
            parts = content.get("parts", []) if isinstance(content, dict) else []
            text = "\n".join(
                part["text"] for part in parts
                if isinstance(part, dict) and isinstance(part.get("text"), str) and not part.get("thought")
            ) if isinstance(parts, list) else ""
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError(f"{self.provider} повернув порожню або некоректну текстову відповідь")
        return self._redact(text.strip())

    def generate(self, prompt: str) -> str:
        path = self._cache_path(prompt)
        cached = self._read_cache(path)
        if cached is not None:
            return cached
        if self.provider == "ollama":
            payload = {"model": self.model, "prompt": prompt, "stream": False, "options": {"temperature": 0}}
            headers = {"Content-Type": "application/json"}
        else:
            key = os.getenv("GEMINI_API_KEY")
            if not key:
                raise RuntimeError("Задайте GEMINI_API_KEY у змінних середовища")
            payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0}}
            headers = {"Content-Type": "application/json", "x-goog-api-key": key}
        request = Request(self.url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        for attempt in range(1, self.max_attempts + 1):
            retry_after, error_payload = None, None
            try:
                with urlopen(request, timeout=180) as response:
                    result = json.load(response)
            except HTTPError as error:
                try:
                    body = error.read().decode("utf-8", errors="replace")
                finally:
                    error.close()
                try:
                    error_payload = json.loads(body)
                except ValueError:
                    error_payload = body
                message = self._api_error(error.code, str(error.reason), error_payload)
                retryable = error.code in RETRYABLE_STATUSES
                retry_after = error.headers.get("Retry-After") if error.headers else None
            except (URLError, TimeoutError, ConnectionError) as error:
                message = self._redact(f"Не вдалося отримати відповідь {self.provider}: мережева помилка: {error}")
                retryable = True
            except (ValueError, UnicodeError):
                raise RuntimeError(f"Некоректна JSON-відповідь {self.provider}") from None
            else:
                text = self._response_text(result)
                self._write_cache(path, text)
                return text
            if not retryable or attempt == self.max_attempts:
                raise RuntimeError(f"{message}; спроб: {attempt}/{self.max_attempts}") from None
            delay = self._retry_delay(attempt, retry_after, error_payload)
            logger.warning("%s; спроба %d/%d, повтор через %.2f с", message, attempt, self.max_attempts, delay)
            time.sleep(delay)
