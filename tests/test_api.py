import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from overwatch.api import APIServer
from overwatch.redfish import RedfishClient, SimulationAdapter
from overwatch.service import Overwatch
from overwatch.simulator import LabServer


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.lab = LabServer(port=0).start()
        self.addCleanup(self.lab.close)
        self.client = RedfishClient(self.lab.url)
        self.app = Overwatch(Path(self.temp.name) / "db.sqlite", b"x" * 32,
                             adapter=SimulationAdapter(self.client), soak_seconds=0)
        self.server = APIServer(self.app, self.client, port=0, identities={
            "operator-secret": {"actor": "sre", "role": "operator"},
            "viewer-secret": {"actor": "reader", "role": "viewer"},
            "admin-secret": {"actor": "security", "role": "policy_admin"}}).start()
        self.addCleanup(self.server.close)

    def request(self, path, data=None, token="operator-secret", **headers):
        req = Request(self.server.url + path, data=json.dumps(data).encode() if data is not None else None,
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", **headers})
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def test_api_requires_authentication(self):
        self.assertEqual(self.request("/api/state", token="wrong")[0], 401)

    def test_foreign_origin_and_host_are_rejected(self):
        self.assertEqual(self.request("/api/state", Origin="https://attacker.invalid")[0], 403)
        self.assertEqual(self.request("/api/state", Host="attacker.invalid")[0], 403)

    def test_role_cannot_be_supplied_by_request_body(self):
        self.request("/api/scenario", {"scenario": "degraded"})
        _, collected = self.request("/api/collect", {})
        _, plan = self.request("/api/plan", {"case_id": collected["cases"][0]["id"]})
        self.request("/api/verify", {"plan_id": plan["id"]})
        status, _ = self.request("/api/approve", {"plan_id": plan["id"], "role": "operator", "actor": "sre"}, token="viewer-secret")
        self.assertEqual(status, 403)

    def test_real_http_workflow_and_postcheck(self):
        self.request("/api/scenario", {"scenario": "degraded"})
        _, collected = self.request("/api/collect", {})
        case_id = collected["cases"][0]["id"]
        _, plan = self.request("/api/plan", {"case_id": case_id})
        self.assertEqual(self.request("/api/verify", {"plan_id": plan["id"]})[0], 200)
        self.assertEqual(self.request("/api/approve", {"plan_id": plan["id"]})[0], 200)
        _, permit = self.request("/api/permit", {"plan_id": plan["id"]})
        _, result = self.request("/api/execute", {"permit_id": permit["id"]})
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self.app.get_case(case_id)["state"], "POST_CHECK")
        self.request("/api/scenario", {"scenario": "recovered"})
        for _ in range(3):
            self.request("/api/collect", {})
        self.assertEqual(self.app.get_case(case_id)["state"], "CLOSED")

    def test_lost_response_over_http_reconciles_without_second_action(self):
        self.request("/api/scenario", {"scenario": "lost_response"})
        _, collected = self.request("/api/collect", {})
        _, plan = self.request("/api/plan", {"case_id": collected["cases"][0]["id"]})
        self.request("/api/verify", {"plan_id": plan["id"]})
        self.request("/api/approve", {"plan_id": plan["id"]})
        _, permit = self.request("/api/permit", {"plan_id": plan["id"]})
        _, result = self.request("/api/execute", {"permit_id": permit["id"]})
        self.assertEqual(result["status"], "unknown")
        _, result = self.request("/api/reconcile", {"operation_id": result["id"]})
        self.assertEqual(result["status"], "succeeded")
        actions = [r for r in self.lab.requests if r[0] == "POST" and "/Actions/" in r[1]]
        self.assertEqual(len(actions), 1)

    def test_only_policy_admin_can_change_controls(self):
        self.assertEqual(self.request("/api/controls", {"kill_switch": True})[0], 403)
        self.assertEqual(self.request("/api/controls", {"kill_switch": True}, token="admin-secret")[0], 200)

    def test_dashboard_and_static_assets_are_served(self):
        for path, marker in [("/", b"WatcherMCP"), ("/app.js", b"api/state"), ("/style.css", b"font-family")]:
            with urlopen(self.server.url + path) as response:
                self.assertIn(marker, response.read())

    def test_unknown_route_returns_json_error(self):
        self.assertEqual(self.request("/api/not-a-route", {})[0], 404)
