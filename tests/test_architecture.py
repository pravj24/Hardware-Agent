"""Architectural fitness checks: preserve the permission boundary as code evolves."""
import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "overwatch"


class ArchitectureTests(unittest.TestCase):
    def test_policy_has_no_network_process_or_model_dependencies(self):
        tree = ast.parse((ROOT / "policy.py").read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                imports.add((node.module or "").split(".")[0])
        forbidden = {"urllib", "requests", "httpx", "socket", "subprocess", "openai", "anthropic", "redfish", "api", "mcp"}
        self.assertEqual(imports & forbidden, set())

    def test_model_tool_surface_cannot_approve_sign_or_execute(self):
        tree = ast.parse((ROOT / "mcp.py").read_text())
        calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(calls & {"execute", "approve", "issue_permit", "set_controls", "put", "event"})

    def test_collector_never_follows_arbitrary_model_supplied_endpoints(self):
        # Tool definitions permit only bounded identity filters; URLs are configuration.
        from overwatch.mcp import TOOL_SETS
        for tools in TOOL_SETS.values():
            self.assertTrue(all(field in {None, "asset_id", "case_id"} for _, _, field in tools.values()))
