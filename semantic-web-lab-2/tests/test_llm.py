import contextlib
import io
import json
import os
import tempfile
import traceback
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from graph_rag_lab import __main__ as cli
from graph_rag_lab.core import Chunk, extract_facts
from graph_rag_lab.llm import HTTPModel


def response(payload):
    return io.BytesIO(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def gemini_response(text):
    return response({"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]})


def http_error(status, payload=None, retry_after=None, reason="API failure"):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    body = payload if isinstance(payload, bytes) else json.dumps(payload or {"error": {"message": "API unavailable"}}).encode()
    return HTTPError("https://example.test/generateContent", status, reason, headers, io.BytesIO(body))


class TestHTTPModel(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.cache = self.directory / "cache"
        self.model = HTTPModel("gemini", cache_dir=self.cache)
        environment = patch.dict(os.environ, {"GEMINI_API_KEY": "test-secret-key", "OLLAMA_BASE_URL": "http://localhost:11434"})
        environment.start()
        self.addCleanup(environment.stop)
        for name in ("urlopen", "time.sleep", "random.uniform", "logger"):
            mock_patch = patch(f"graph_rag_lab.llm.{name}")
            mock = mock_patch.start()
            self.addCleanup(mock_patch.stop)
            setattr(self, name.split(".")[-1], mock)
        self.uniform.return_value = 0

    def test_defaults_and_explicit_model(self):
        self.assertEqual(self.model.model, "gemini-3.5-flash-lite")
        self.assertEqual(HTTPModel("ollama").model, "gemma3:4b")
        self.assertEqual(HTTPModel("gemini", "another-model").model, "another-model")
        with self.assertRaises(ValueError):
            HTTPModel("gemini", max_attempts=0)

    def test_retryable_http_statuses(self):
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.urlopen.reset_mock()
                self.sleep.reset_mock()
                self.urlopen.side_effect = [http_error(status), gemini_response("Відповідь")]
                self.assertEqual(self.model.generate(f"Питання {status}"), "Відповідь")
                self.assertEqual(self.urlopen.call_count, 2)
                self.sleep.assert_called_once_with(2)
                first, second = [call.args[0] for call in self.urlopen.call_args_list]
                self.assertEqual(first.data, second.data)
                self.assertNotIn("test-secret-key", first.full_url)
                self.assertEqual(first.get_header("X-goog-api-key"), "test-secret-key")

    def test_bounded_exponential_backoff_and_failed_request_not_cached(self):
        model = HTTPModel("gemini", cache_dir=self.cache, max_attempts=8)
        self.urlopen.side_effect = [http_error(503) for _ in range(8)]
        with self.assertRaisesRegex(RuntimeError, "HTTP 503.*спроб: 8/8"):
            model.generate("Питання")
        self.assertEqual(self.urlopen.call_count, 8)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [2, 4, 8, 16, 32, 60, 60])
        self.assertEqual(list(self.cache.glob("*.json")), [])
        self.urlopen.side_effect = [gemini_response("Відновлено")]
        self.assertEqual(model.generate("Питання"), "Відновлено")
        self.assertEqual(len(list(self.cache.glob("*.json"))), 1)

    def test_non_retryable_http_statuses(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                self.urlopen.reset_mock()
                self.urlopen.side_effect = [http_error(status, {"error": {"message": "Invalid request"}})]
                with self.assertRaisesRegex(RuntimeError, f"HTTP {status}.*Invalid request.*спроб: 1/5"):
                    self.model.generate(f"Питання {status}")
                self.urlopen.assert_called_once()
        self.sleep.assert_not_called()
        self.assertEqual(list(self.cache.glob("*.json")), [])

    def test_retry_after_seconds_http_date_and_invalid_values(self):
        cases = [("12", 12), ("Thu, 01 Jan 1970 00:01:40 GMT", 10), ("invalid", 2), ("NaN", 2), ("-1", 2)]
        for header, expected in cases:
            with self.subTest(header=header), patch("graph_rag_lab.llm.time.time", return_value=90):
                self.sleep.reset_mock()
                self.urlopen.side_effect = [http_error(429, retry_after=header), gemini_response("OK")]
                self.assertEqual(self.model.generate(header), "OK")
                self.sleep.assert_called_once_with(expected)

    def test_retry_info_and_quota_diagnostics_without_secrets(self):
        payload = {"error": {
            "code": 429, "status": "RESOURCE_EXHAUSTED",
            "message": "Quota exceeded for test-secret-key; key=test-secret-key",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{
                    "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                    "quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
                    "quotaDimensions": {"model": "gemini-3.5-flash-lite"}, "quotaValue": "5",
                }]},
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12.5s"},
            ],
        }}
        model = HTTPModel("gemini", cache_dir=self.cache, max_attempts=2)
        self.urlopen.side_effect = [http_error(429, payload, reason="key=test-secret-key") for _ in range(2)]
        with self.assertRaises(RuntimeError) as caught:
            model.generate("Питання")
        message = str(caught.exception)
        for expected in ("HTTP 429", "RESOURCE_EXHAUSTED", "Quota exceeded", "quotaMetric", "quotaId", "quotaValue", "12.5s"):
            self.assertIn(expected, message)
        logs = "\n".join(call.args[0] % call.args[1:] for call in self.logger.warning.call_args_list)
        formatted = "".join(traceback.format_exception(caught.exception))
        for output in (message, logs, formatted):
            self.assertNotIn("test-secret-key", output)
        self.assertTrue(caught.exception.__suppress_context__)
        self.sleep.assert_called_once_with(12.5)
        self.assertEqual(list(self.cache.glob("*.json")), [])

    def test_non_json_http_error(self):
        self.urlopen.side_effect = [http_error(503, b"<html>Service Unavailable</html>")]
        model = HTTPModel("gemini", cache_dir=self.cache, max_attempts=1)
        with self.assertRaisesRegex(RuntimeError, "HTTP 503.*Service Unavailable"):
            model.generate("Питання")
        self.sleep.assert_not_called()

    def test_network_failures_are_bounded_and_retried(self):
        self.urlopen.side_effect = [URLError("connection reset test-secret-key"), TimeoutError("timed out"), gemini_response("OK")]
        self.assertEqual(self.model.generate("Питання"), "OK")
        self.assertEqual(self.urlopen.call_count, 3)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [2, 4])
        self.assertNotIn("test-secret-key", str(self.logger.warning.call_args_list))

    def test_persistent_cache_separates_prompt_model_provider_and_endpoint(self):
        self.urlopen.side_effect = [
            gemini_response("Перша"), gemini_response("Інший промпт"), gemini_response("Інша модель"),
            response({"response": "Ollama"}), response({"response": "Інший сервер"}),
        ]
        self.assertEqual(self.model.generate("Промпт"), "Перша")
        self.assertEqual(self.model.generate("Промпт"), "Перша")
        restarted = HTTPModel("gemini", cache_dir=self.cache)
        self.assertEqual(restarted.generate("Промпт"), "Перша")
        self.assertEqual(self.urlopen.call_count, 1)
        self.assertEqual(restarted.generate("Інший промпт"), "Інший промпт")
        other_model = HTTPModel("gemini", "custom", cache_dir=self.cache)
        self.assertEqual(other_model.generate("Промпт"), "Інша модель")
        ollama = HTTPModel("ollama", self.model.model, cache_dir=self.cache)
        self.assertEqual(ollama.generate("Промпт"), "Ollama")
        with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://another-server:11434"}):
            self.assertEqual(HTTPModel("ollama", self.model.model, cache_dir=self.cache).generate("Промпт"), "Інший сервер")
        self.assertEqual(self.urlopen.call_count, 5)
        self.assertEqual(len(list(self.cache.iterdir())), 5)

    def test_cache_does_not_store_prompt_or_api_key(self):
        self.urlopen.return_value = gemini_response("Відповідь test-secret-key")
        self.assertEqual(self.model.generate("Приватний промпт test-secret-key"), "Відповідь [REDACTED]")
        files = list(self.cache.iterdir())
        self.assertEqual(len(files), 1)
        entry = files[0].read_text(encoding="utf-8")
        self.assertNotIn("test-secret-key", entry)
        self.assertNotIn("Приватний промпт", entry)
        self.assertRegex(files[0].name, r"^[0-9a-f]{64}\.json$")
        self.assertEqual(json.loads(entry), {"text": "Відповідь [REDACTED]"})

    def test_corrupt_cache_is_replaced(self):
        for corrupt in ("not json", "[]", '{"text": ""}'):
            with self.subTest(corrupt=corrupt):
                path = self.model._cache_path("Питання")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(corrupt, encoding="utf-8")
                self.urlopen.side_effect = [gemini_response("Відновлено")]
                self.assertEqual(self.model.generate("Питання"), "Відновлено")
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["text"], "Відновлено")

    def test_invalid_api_responses_are_not_cached(self):
        invalid = [
            {"error": {"message": "API error"}}, [], {}, {"candidates": [None]},
            {"promptFeedback": {"blockReason": "SAFETY"}},
            {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "partial"}]}}]},
            {"candidates": [{"finishReason": "SAFETY"}]},
            {"candidates": [{"content": {"parts": [{"text": " "}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "thinking", "thought": True}]}}]},
        ]
        for index, payload in enumerate(invalid):
            with self.subTest(payload=payload):
                self.urlopen.side_effect = [response(payload), gemini_response("OK")]
                prompt = f"Питання {index}"
                with self.assertRaises(RuntimeError):
                    self.model.generate(prompt)
                self.assertFalse(self.model._cache_path(prompt).exists())
                self.assertEqual(self.model.generate(prompt), "OK")
        self.sleep.assert_not_called()

    def test_malformed_json_response_is_not_cached(self):
        self.urlopen.side_effect = [io.BytesIO(b"not JSON"), gemini_response("OK")]
        with self.assertRaisesRegex(RuntimeError, "JSON"):
            self.model.generate("Питання")
        self.assertEqual(list(self.cache.glob("*.json")), [])
        self.assertEqual(self.model.generate("Питання"), "OK")

    def test_invalid_facts_are_evicted_and_can_be_regenerated(self):
        chunks = [Chunk("doc:0", "doc", "Текст документа")]
        for invalid in ("not facts JSON", '[{"subject":"A"}]', '[{"doc":"unknown","subject":"A","predicate":"uses","object":"B"}]'):
            with self.subTest(invalid=invalid):
                self.urlopen.side_effect = [gemini_response(invalid)]
                with self.assertRaises(ValueError):
                    extract_facts(chunks, self.model)
                self.assertEqual(list(self.cache.glob("*.json")), [])
        self.urlopen.side_effect = [gemini_response('[{"subject":"A","predicate":"uses","object":"B"}]')]
        self.assertEqual(extract_facts(chunks, self.model), [{"doc": "doc", "subject": "A", "predicate": "uses", "object": "B"}])

    def test_cli_resumes_after_failure_during_community_summary(self):
        chunks = [Chunk("one:0", "one", "Сервіс використовує чергу."), Chunk("two:0", "two", "Моніторинг збирає метрики.")]
        arguments = ["graph_rag_lab", "run", "--provider", "gemini", "--max-attempts", "2",
                     "--cache-dir", str(self.cache), "--output", str(self.directory / "output")]
        self.urlopen.side_effect = [
            gemini_response('[{"subject":"A","predicate":"uses","object":"B"}]'),
            gemini_response('[{"subject":"C","predicate":"uses","object":"D"}]'),
            gemini_response("Резюме першої спільноти"), http_error(429), http_error(503),
        ]
        stderr = io.StringIO()
        with patch("sys.argv", arguments), patch.object(cli, "load_chunks", return_value=chunks):
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
                cli.main()
            self.assertEqual(caught.exception.code, 1)
            self.assertIn("HTTP 503", stderr.getvalue())
            self.assertIn("Повторіть ту саму команду", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())
            self.assertEqual(self.urlopen.call_count, 5)
            self.assertTrue((self.directory / "output" / "facts.json").exists())
            self.assertFalse((self.directory / "output" / "answers.json").exists())
            failed_prompt = self.urlopen.call_args.args[0].data
            self.urlopen.side_effect = [gemini_response("Резюме другої спільноти"), gemini_response("GraphRAG"), gemini_response("vector-RAG")]
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                cli.main()
            self.assertEqual(self.urlopen.call_count, 8)
            self.assertEqual(self.urlopen.call_args_list[5].args[0].data, failed_prompt)
            result = json.loads((self.directory / "output" / "answers.json").read_text(encoding="utf-8"))
            self.assertEqual(result["model"], "gemini-3.5-flash-lite")
            self.assertEqual([item["summary"] for item in result["graph_rag"]["summaries"]], ["Резюме першої спільноти", "Резюме другої спільноти"])
            self.assertEqual(result["graph_rag"]["answer"], "GraphRAG")
            self.assertEqual(result["vector_rag"]["answer"], "vector-RAG")
            self.assertTrue((self.directory / "output" / "comparison.md").exists())
            self.assertIn("Відповіді:", stdout.getvalue())
            self.urlopen.side_effect = AssertionError("Повторний виклик API після завершення")
            with contextlib.redirect_stdout(io.StringIO()):
                cli.main()
            self.assertEqual(self.urlopen.call_count, 8)

    def test_cli_missing_key_and_invalid_attempt_limit(self):
        arguments = ["graph_rag_lab", "run", "--provider", "gemini", "--cache-dir", str(self.cache)]
        with patch("sys.argv", arguments), patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
                cli.main()
            self.assertEqual(caught.exception.code, 1)
            self.assertIn("GEMINI_API_KEY", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())
        with patch("sys.argv", arguments + ["--max-attempts", "0"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                cli.main()
            self.assertEqual(caught.exception.code, 2)
        self.urlopen.assert_not_called()

    def test_cli_help_and_model_override(self):
        stdout = io.StringIO()
        with patch("sys.argv", ["graph_rag_lab", "--help"]), contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as caught:
                cli.main()
            self.assertEqual(caught.exception.code, 0)
        for text in ("--cache-dir", "--max-attempts", "gemini-3.5-flash-lite"):
            self.assertIn(text, stdout.getvalue())
        with patch("sys.argv", ["graph_rag_lab", "run", "--provider", "gemini", "--model", "custom-model"]):
            with patch.object(cli, "run") as run:
                cli.main()
            self.assertEqual(run.call_args.args[0].model, "custom-model")
        self.urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
