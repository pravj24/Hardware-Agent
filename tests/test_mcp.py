import io
import json
import tempfile
import unittest
from pathlib import Path

from overwatch.mcp import MCPServer
from overwatch.service import Overwatch


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Overwatch(Path(self.temp.name) / "db.sqlite", b"m" * 32)
        self.server = MCPServer(self.app.store, "fleet")

    def initialize(self):
        response = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_lifecycle_requires_initialization(self):
        self.assertIn("error", self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))

    def test_fleet_zone_exposes_no_action_or_approval_tool(self):
        self.initialize()
        tools = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        self.assertTrue(tools)
        self.assertTrue(all(t["name"].startswith("fleet.") and t["annotations"]["readOnlyHint"] for t in tools))
        result = self.server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "action.execute", "arguments": {}}})
        self.assertIn("error", result)

    def test_tool_output_has_receipts_but_no_permit_or_signing_secret(self):
        self.initialize()
        result = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "fleet.get_observations", "arguments": {}}})["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]), [])

    def test_stdio_is_newline_delimited_json_only(self):
        messages = [dict(jsonrpc="2.0", id=1, method="initialize", params={"protocolVersion": "2025-06-18"}),
                    dict(jsonrpc="2.0", method="notifications/initialized"),
                    dict(jsonrpc="2.0", id=2, method="tools/list")]
        source = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")
        target = io.StringIO()
        self.server.run(source, target)
        responses = [json.loads(line) for line in target.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in responses], [1, 2])

    def test_unknown_arguments_are_rejected(self):
        self.initialize()
        response = self.server.handle(dict(jsonrpc="2.0", id=2, method="tools/call", params={
            "name": "fleet.get_observations", "arguments": {"url": "https://attacker.invalid"}}))
        self.assertIn("error", response)
