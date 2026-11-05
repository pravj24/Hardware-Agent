"""Verification tests for the WatcherMCP HTTP/SSE Web Service Gateway."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from deploy.mcp_web_server import MCPWebServer, seed_demo_records  # noqa: E402
from overwatch.store import Store  # noqa: E402


class MCPWebDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "overwatch.db"
        self.store = Store(self.db_path)
        seed_demo_records(self.store)
        self.server = MCPWebServer(
            self.store,
            host="127.0.0.1",
            port=0,
            zones=("fleet", "records", "market"),
            stateless_fallback=True,
        ).start()
        self.addCleanup(self.server.close)

    def _request_json(self, path: str, payload: dict | None = None, headers: dict[str, str] | None = None):
        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"{self.server.url}{path}",
            data=data,
            headers=req_headers,
            method="POST" if payload is not None else "GET",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, dict(resp.headers), (json.loads(body) if body else None)

    def test_health_and_discovery_endpoints(self):
        status, _, health = self._request_json("/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        self.assertTrue(health["ledger_valid"])
        self.assertEqual(health["zones"], ["fleet", "records", "market"])

        status, _, discovery = self._request_json("/mcp")
        self.assertEqual(status, 200)
        self.assertIn("fleet", discovery["zones"])
        self.assertIn("records", discovery["zones"])
        self.assertIn("market", discovery["zones"])

    def test_full_mcp_session_lifecycle_over_http(self):
        status, headers, init_resp = self._request_json("/mcp/fleet", {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        })
        self.assertEqual(status, 200)
        self.assertEqual(init_resp["result"]["protocolVersion"], "2025-06-18")
        session_id = headers.get("Mcp-Session-Id") or headers.get("mcp-session-id")
        self.assertTrue(session_id)

        status, _, notif_resp = self._request_json(
            "/mcp/fleet",
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Mcp-Session-Id": session_id},
        )
        self.assertEqual(status, 202)
        self.assertIsNone(notif_resp)

        status, _, list_resp = self._request_json(
            "/mcp/fleet",
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={"Mcp-Session-Id": session_id},
        )
        self.assertEqual(status, 200)
        tools = list_resp["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["fleet.get_observations"])

        status, _, call_resp = self._request_json(
            "/mcp/fleet",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "fleet.get_observations", "arguments": {"asset_id": "rack1-node01"}},
            },
            headers={"Mcp-Session-Id": session_id},
        )
        self.assertEqual(status, 200)
        observations = json.loads(call_resp["result"]["content"][0]["text"])
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["body"]["asset_id"], "rack1-node01")
        self.assertNotIn("source", observations[0]["body"])

    def test_zone_isolation_across_fleet_records_and_market(self):
        # Records zone serves cases and plans, but rejects fleet tools
        _, _, records_call = self._request_json("/mcp/records", {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "records.get_cases", "arguments": {}},
        })
        cases = json.loads(records_call["result"]["content"][0]["text"])
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["id"], "case_seed_001")

        _, _, plans_call = self._request_json("/mcp/records", {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "records.get_plans", "arguments": {"case_id": "case_seed_001"}},
        })
        plans = json.loads(plans_call["result"]["content"][0]["text"])
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["id"], "plan_seed_001")

        _, _, cross_zone_err = self._request_json("/mcp/records", {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {"name": "fleet.get_observations", "arguments": {}},
        })
        self.assertIn("error", cross_zone_err)

        # Market zone serves tenders
        _, _, market_call = self._request_json("/mcp/market", {
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {"name": "market.get_tenders", "arguments": {}},
        })
        tenders = json.loads(market_call["result"]["content"][0]["text"])
        self.assertEqual(len(tenders), 1)
        self.assertEqual(tenders[0]["id"], "tndr_seed_001")

    def test_sse_endpoint_and_message_streaming(self):
        req = urllib.request.Request(
            f"{self.server.url}/mcp/fleet/sse",
            headers={"Accept": "text/event-stream"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/event-stream", resp.headers.get("Content-Type", ""))
            event_line = resp.readline().decode("utf-8").strip()
            data_line = resp.readline().decode("utf-8").strip()
            blank_line = resp.readline().decode("utf-8").strip()
            self.assertEqual(event_line, "event: endpoint")
            self.assertTrue(data_line.startswith("data: /mcp/fleet/messages?session_id="))
            self.assertEqual(blank_line, "")
            messages_path = data_line.split("data: ", 1)[1]

            # Post a ping request to the SSE session's messages endpoint and read it from the SSE stream
            status, _, post_resp = self._request_json(messages_path, {
                "jsonrpc": "2.0",
                "id": 99,
                "method": "ping",
            })
            self.assertEqual(status, 200)
            self.assertEqual(post_resp["id"], 99)

            msg_event = resp.readline().decode("utf-8").strip()
            msg_data = resp.readline().decode("utf-8").strip()
            self.assertEqual(msg_event, "event: message")
            parsed_sse_msg = json.loads(msg_data.split("data: ", 1)[1])
            self.assertEqual(parsed_sse_msg["id"], 99)
            self.assertEqual(parsed_sse_msg["result"], {})

    def test_bearer_token_authentication(self):
        secured = MCPWebServer(
            self.store,
            host="127.0.0.1",
            port=0,
            zones=("fleet",),
            token="secret-test-token",
        ).start()
        self.addCleanup(secured.close)

        req = urllib.request.Request(f"{secured.url}/mcp/fleet", method="GET")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 401)

        authed_req = urllib.request.Request(
            f"{secured.url}/mcp/fleet",
            headers={"Authorization": "Bearer secret-test-token"},
            method="GET",
        )
        with urllib.request.urlopen(authed_req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)


if __name__ == "__main__":
    unittest.main()
