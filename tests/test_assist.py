import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from overwatch.assist import (Assistant, ClaudeClient, StubClient, TOOL_NAMES,
                              ZhipuClient, choose_assist_client)
from overwatch.discovery import FixtureDiscoveryClient
from overwatch.service import Overwatch


class FakeResponse:
    def __init__(self, data):
        self._data = json.dumps(data).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, responses):
        self.responses, self.requests, self.calls = list(responses), [], 0

    def __call__(self, request, timeout=None):
        self.requests.append(json.loads(request.data))
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return FakeResponse(response)


def zhipu_tool_call(name, args):
    return {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)}}]}}]}


def zhipu_text(content):
    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}]}


class ScriptedClient:
    """Returns pre-scripted turns so the tool loop runs deterministically, no network."""

    def __init__(self, turns):
        self.turns, self.calls = list(turns), 0
        self.model = "scripted"

    def complete(self, *, system, messages, tools):
        self.system_seen = system
        turn = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        return {"stop_reason": turn["stop_reason"], "blocks": turn["blocks"],
                "assistant_content": turn["blocks"]}


def text(s):
    return {"stop_reason": "end_turn", "blocks": [{"type": "text", "text": s}]}


def tool(name, args, tid="t1"):
    return {"stop_reason": "tool_use",
            "blocks": [{"type": "tool_use", "id": tid, "name": name, "input": args}]}


class AssistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Overwatch(Path(self.temp.name) / "db.sqlite", b"x" * 32, clock=lambda: 1000.0)
        self.identity = {"actor": "sre", "role": "operator"}

    def assistant(self, turns):
        return Assistant(self.app, client=ScriptedClient(turns), discovery=FixtureDiscoveryClient())

    def test_only_read_or_draft_tools_are_exposed(self):
        self.assertEqual(TOOL_NAMES, {"discover_providers", "get_case_evidence", "get_recent_tenders"})
        self.assertFalse(TOOL_NAMES & {"execute", "approve", "issue_permit", "action.request", "set_controls"})

    def test_discovery_tool_runs_then_model_drafts(self):
        result = self.assistant([
            tool("discover_providers", {"need_query": "data center hardware maintenance"}),
            text("Draft: three indicative leads found; please review in the dashboard."),
        ]).run(prompt="find maintainers", identity=self.identity)
        self.assertEqual(result["tools_used"], ["discover_providers"])
        self.assertEqual(result["status"], "DRAFT_ONLY")
        leads = result["evidence"][0]["output"]["leads"]
        self.assertEqual({l["name"] for l in leads}, {"Evernex", "SSCS", "Top Ten USA, LLC."})
        self.assertIn("review", result["reply"])

    def test_case_evidence_tool_returns_receipt_figures_not_invented(self):
        case = self.app.observe("gpu-1", 91.0, 999.0, source="lab", event_id="e1")
        result = self.assistant([
            tool("get_case_evidence", {"case_id": case["id"]}),
            text("Draft maintenance message referencing the receipt."),
        ]).run(prompt="draft a maintenance message", identity=self.identity)
        out = result["evidence"][0]["output"]
        self.assertEqual(out["case"]["asset_id"], "gpu-1")
        self.assertEqual(out["receipts"][0]["temperature"], 91.0)
        self.assertTrue(out["receipts"][0]["sha256"])

    def test_unknown_case_is_reported_not_fabricated(self):
        result = self.assistant([
            tool("get_case_evidence", {"case_id": "missing"}),
            text("I could not find that case."),
        ]).run(prompt="draft", identity=self.identity)
        self.assertEqual(result["evidence"][0]["output"], {"error": "case not found"})

    def test_loop_is_bounded_when_model_never_stops(self):
        looping = Assistant(self.app, client=ScriptedClient([tool("get_recent_tenders", {})]),
                            discovery=FixtureDiscoveryClient(), max_steps=3)
        result = looping.run(prompt="loop", identity=self.identity)
        self.assertEqual(len(result["tools_used"]), 3)

    def test_assist_invocation_is_audited(self):
        self.assistant([text("done")]).run(prompt="hello", task="draft_message", identity=self.identity)
        kinds = [e["kind"] for e in self.app.store.events()]
        self.assertIn("assist_drafted", kinds)

    def test_stub_client_works_without_a_key(self):
        result = Assistant(self.app, client=StubClient()).run(prompt="draft something", identity=self.identity)
        self.assertEqual(result["tools_used"], [])
        self.assertIn("stub mode", result["reply"])

    def test_zhipu_translates_request_and_parses_a_tool_call(self):
        opener = FakeOpener([zhipu_tool_call("discover_providers", {"need_query": "maintenance"})])
        client = ZhipuClient("key", opener=opener)
        step = client.complete(system="SYS", messages=[{"role": "user", "content": "find"}],
                               tools=[{"name": "discover_providers", "description": "d",
                                       "input_schema": {"type": "object"}}])
        self.assertEqual(step["stop_reason"], "tool_use")
        self.assertEqual(step["blocks"][0]["name"], "discover_providers")
        self.assertEqual(step["blocks"][0]["input"], {"need_query": "maintenance"})
        sent = opener.requests[0]
        self.assertEqual(sent["model"], "glm-4.6")
        self.assertEqual(sent["messages"][0], {"role": "system", "content": "SYS"})
        self.assertEqual(sent["tools"][0]["type"], "function")

    def test_zhipu_plain_text_response_ends_the_turn(self):
        client = ZhipuClient("key", opener=FakeOpener([zhipu_text("Draft ready.")]))
        step = client.complete(system="SYS", messages=[{"role": "user", "content": "hi"}], tools=[])
        self.assertEqual(step["stop_reason"], "end_turn")
        self.assertEqual(step["blocks"][0]["text"], "Draft ready.")

    def test_zhipu_tool_results_become_tool_role_messages(self):
        messages = [{"role": "user", "content": "go"},
                    {"role": "assistant", "content": [{"type": "tool_use", "id": "c1",
                        "name": "get_recent_tenders", "input": {}}]},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1",
                        "content": "{}"}]}]
        openai_messages, _ = ZhipuClient._to_openai("SYS", messages, [])
        self.assertEqual(openai_messages[2]["tool_calls"][0]["id"], "c1")
        self.assertEqual(openai_messages[3], {"role": "tool", "tool_call_id": "c1", "content": "{}"})

    def test_zhipu_runs_the_full_tool_loop(self):
        opener = FakeOpener([zhipu_tool_call("discover_providers", {"need_query": "maintenance"}),
                             zhipu_text("Three indicative leads; review in the dashboard.")])
        assistant = Assistant(self.app, client=ZhipuClient("key", opener=opener),
                              discovery=FixtureDiscoveryClient())
        result = assistant.run(prompt="find maintainers", identity=self.identity)
        self.assertEqual(result["tools_used"], ["discover_providers"])
        self.assertIn("review", result["reply"])
        self.assertEqual({l["name"] for l in result["evidence"][0]["output"]["leads"]},
                         {"Evernex", "SSCS", "Top Ten USA, LLC."})

    def test_choose_client_prefers_zhipu_then_claude_then_stub(self):
        with mock.patch.dict("os.environ", {"ZHIPU_API_KEY": "z", "ANTHROPIC_API_KEY": "a"}, clear=False):
            self.assertIsInstance(choose_assist_client(), ZhipuClient)
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "a"}, clear=True):
            self.assertIsInstance(choose_assist_client(), ClaudeClient)
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsInstance(choose_assist_client(), StubClient)

    def test_zhipu_requires_a_key(self):
        with self.assertRaises(Exception):
            ZhipuClient("")

    def test_zhipu_surfaces_the_api_error_body(self):
        import io
        import urllib.error
        from overwatch.errors import Rejected

        def opener(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {},
                io.BytesIO(json.dumps({"error": {"code": "1113",
                    "message": "Insufficient balance or no resource package."}}).encode()))

        client = ZhipuClient("key", opener=opener)
        with self.assertRaises(Rejected) as caught:
            client.complete(system="s", messages=[{"role": "user", "content": "hi"}], tools=[])
        self.assertIn("1113", str(caught.exception))
        self.assertIn("Insufficient balance", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
