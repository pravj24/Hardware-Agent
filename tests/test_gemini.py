"""Gemini REST contracts use scripted responses; no API key or paid calls in CI."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

from overwatch.assist import Assistant, GeminiClient, StubClient, choose_assist_client
from overwatch.discovery import FixtureDiscoveryClient
from overwatch.errors import Rejected
from overwatch.service import Overwatch


def response(*parts, finish="STOP"):
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)}, "finishReason": finish}]}


class Transport:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, request, timeout):
        self.requests.append(request)
        return io.BytesIO(json.dumps(self.responses.pop(0)).encode())


class GeminiTests(unittest.TestCase):
    def call(self, client):
        return client.complete(system="Only draft", messages=[{"role": "user", "content": "hello"}], tools=[])

    def test_key_is_in_header_never_url(self):
        transport = Transport(response({"text": "Draft ready"}))
        step = self.call(GeminiClient("test-secret", opener=transport, model="gemini-test"))
        request = transport.requests[0]
        self.assertEqual(request.get_header("X-goog-api-key"), "test-secret")
        self.assertNotIn("test-secret", request.full_url)
        self.assertEqual(request.full_url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent")
        self.assertEqual(step["blocks"], [{"type": "text", "text": "Draft ready"}])

    def test_function_call_roundtrip_preserves_thought_signature_and_id(self):
        part = {"functionCall": {"name": "discover_providers", "args": {"need_query": "maintenance"}, "id": "google-call-1"},
                "thoughtSignature": "opaque-signature"}
        transport = Transport(response(part), response({"text": "Draft: review the indicative leads."}))
        with tempfile.TemporaryDirectory() as temp:
            app = Overwatch(Path(temp) / "state.db", b"g" * 32)
            result = Assistant(app, client=GeminiClient("test", opener=transport),
                               discovery=FixtureDiscoveryClient()).run(prompt="find service", identity={"actor": "sre"})
        self.assertEqual(result["tools_used"], ["discover_providers"])
        sent = json.loads(transport.requests[1].data)
        self.assertEqual(sent["contents"][1]["parts"], [part])
        tool_result = sent["contents"][2]["parts"][0]["functionResponse"]
        self.assertEqual(tool_result["id"], "google-call-1")
        self.assertEqual(tool_result["name"], "discover_providers")
        self.assertEqual(len(tool_result["response"]["leads"]), 3)
        self.assertEqual(sent["systemInstruction"]["parts"][0]["text"][:7], "You are")
        names = {f["name"] for f in sent["tools"][0]["functionDeclarations"]}
        self.assertNotIn("execute", names)

    def test_parallel_tool_calls_get_distinct_local_ids_when_google_omits_them(self):
        transport = Transport(response({"functionCall": {"name": "get_recent_tenders", "args": {}}},
                                       {"functionCall": {"name": "get_case_evidence", "args": {"case_id": "x"}}}))
        step = self.call(GeminiClient("test", opener=transport))
        self.assertEqual(len({b["id"] for b in step["blocks"]}), 2)

    def test_empty_blocked_malformed_and_truncated_responses_fail_closed(self):
        for payload in [{"promptFeedback": {"blockReason": "SAFETY"}}, response(),
                        {"candidates": {"unexpected": "mapping"}},
                        response({"functionCall": {"name": "get_recent_tenders", "args": {}, "id": 42}}),
                        response({"text": "partial"}, finish="MAX_TOKENS"),
                        response({"functionCall": {"name": "get_recent_tenders", "args": []}})]:
            with self.subTest(payload=payload), self.assertRaises(Rejected):
                self.call(GeminiClient("test", opener=Transport(payload)))

    def test_thought_text_is_not_exposed_as_user_facing_draft(self):
        step = self.call(GeminiClient("test", opener=Transport(response(
            {"text": "internal thought", "thought": True}, {"text": "Draft only"}))))
        self.assertEqual(step["blocks"], [{"type": "text", "text": "Draft only"}])

    def test_transport_error_does_not_echo_response_or_secret(self):
        def failing(request, timeout):
            raise HTTPError(request.full_url, 429, "limit", {}, io.BytesIO(b'{"error":{"message":"test-secret"}}'))
        with self.assertRaises(Rejected) as caught:
            self.call(GeminiClient("test-secret", opener=failing))
        self.assertIn("429", str(caught.exception))
        self.assertNotIn("test-secret", str(caught.exception))

    def test_provider_selection_is_gemini_or_explicit_offline_mode(self):
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "test", "GEMINI_MODEL": "gemini-test"}, clear=True):
            client = choose_assist_client()
            self.assertIsInstance(client, GeminiClient)
            self.assertEqual(client.model, "gemini-test")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsInstance(choose_assist_client(), StubClient)

    def test_no_key_or_invalid_model_is_rejected(self):
        with self.assertRaises(Rejected):
            GeminiClient("")
        with self.assertRaises(Rejected):
            GeminiClient("test", model="../../attacker?key=x")

    def test_unknown_or_malformed_tool_calls_do_not_escape_the_loop(self):
        transport = Transport(response({"functionCall": {"name": "approve", "args": {}}}),
                              response({"text": "Draft: permission unavailable."}))
        with tempfile.TemporaryDirectory() as temp:
            app = Overwatch(Path(temp) / "state.db", b"g" * 32)
            result = Assistant(app, client=GeminiClient("test", opener=transport)).run(prompt="approve", identity={"actor": "sre"})
            self.assertEqual(app.store.list("approvals"), [])
        self.assertIn("error", result["evidence"][0]["output"])
