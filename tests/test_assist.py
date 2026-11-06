import tempfile
import unittest
from pathlib import Path

from overwatch.assist import Assistant, StubClient, TOOL_NAMES
from overwatch.discovery import FixtureDiscoveryClient
from overwatch.service import Overwatch


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



if __name__ == "__main__":
    unittest.main()
