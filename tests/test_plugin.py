"""Exercise the packaged Codex commands over real stdio, outside the checkout."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from deploy.mcp_web_server import seed_demo_records
from overwatch.store import Store


ROOT = Path(__file__).resolve().parents[1]


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="watcher plugin ")
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name)
        self.db = self.cwd / "evidence store.db"
        seed_demo_records(Store(self.db))
        marketplace = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
        entry = next(p for p in marketplace["plugins"] if p["name"] == "watchermcp")
        self.plugin = ROOT / entry["source"]
        manifest = json.loads((self.plugin / ".codex-plugin/plugin.json").read_text())
        self.servers = json.loads((self.plugin / manifest["mcpServers"]).read_text())["mcpServers"]

    def run_server(self, name, requests=(), *, db=None, extra_args=()):
        config = self.servers[name]
        # Emulate the host's declared environment allowlist. PYTHONPATH makes the
        # source package importable like an editable install without changing the
        # developer's Python environment. Use this test runner's interpreter.
        env = {k: v for k, v in os.environ.items() if k != "WATCHER_MCP_DB"}
        env["PYTHONPATH"] = str(ROOT)
        if db is not None and "WATCHER_MCP_DB" in config.get("env_vars", []):
            env["WATCHER_MCP_DB"] = str(db)
        self.assertEqual(config["command"], "python")
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "plugin-test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            *requests,
        ]
        return subprocess.run(
            [sys.executable, *config["args"], *extra_args], cwd=self.cwd, env=env,
            input="".join(json.dumps(m) + "\n" for m in messages),
            capture_output=True, text=True, timeout=20,
        )

    def test_all_packaged_servers_read_evidence_and_reject_actions(self):
        expected = {
            "watcher-fleet": {"fleet.get_observations": "rcpt_seed_001"},
            "watcher-records": {"records.get_cases": "case_seed_001",
                                "records.get_plans": "plan_seed_001"},
            "watcher-market": {"market.get_tenders": "tndr_seed_001"},
        }
        self.assertEqual(set(self.servers), set(expected))
        for name, tools in expected.items():
            with self.subTest(server=name):
                requests = [{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
                for i, tool in enumerate([*tools, "action.execute"], 3):
                    requests.append({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                     "params": {"name": tool, "arguments": {}}})
                proc = self.run_server(name, requests, db=self.db)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                responses = [json.loads(line) for line in proc.stdout.splitlines()]
                advertised = responses[1]["result"]["tools"]
                self.assertEqual({t["name"] for t in advertised}, set(tools))
                self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in advertised))
                for response, expected_id in zip(responses[2:-1], tools.values(), strict=True):
                    records = json.loads(response["result"]["content"][0]["text"])
                    self.assertIn(expected_id, [r["id"] for r in records])
                self.assertEqual(responses[-1]["error"]["code"], -32602)

    def test_explicit_database_overrides_environment(self):
        proc = self.run_server("watcher-fleet", db=self.cwd / "missing.db",
                               extra_args=["--db", str(self.db)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("result", json.loads(proc.stdout))

    def test_missing_database_fails_without_creating_it(self):
        missing = self.cwd / "missing.db"
        proc = self.run_server("watcher-fleet", db=missing)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertIn("database not found", proc.stderr)
        self.assertFalse(missing.exists())

    def test_unset_environment_uses_working_directory_default(self):
        fallback = self.cwd / "data/overwatch.db"
        seed_demo_records(Store(fallback))
        proc = self.run_server("watcher-fleet")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("result", json.loads(proc.stdout))
